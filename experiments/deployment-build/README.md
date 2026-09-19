# Deployment-build experiment: runbook

Protocol: `docs/deployment-experiment.md`, frozen at `2bbaaec`. **Read it before running anything here.** This directory holds the pre-registration artifacts and the driver; it does not restate the design.

## Why a driver exists

`rb run --baseline A --candidate B` compares **two checkpoints under one configuration**. This experiment needs **one checkpoint under two configurations**, which the shipped command cannot express. `build_compare.py` supplies that, and does it by calling `rabbit_brain.runner.evaluate_model`, the same function `rb run` calls once per checkpoint. Every per-case error and trajectory is produced by the product's own evaluation path; nothing here reimplements a metric.

Output is a version-1 comparison that `rb import` accepts, so findings, ranking, borderline marking, saved checks, the report and the receipt all work normally.

Verified end to end on the synthetic adapter before any GPU was rented: 24 cases, hooks recorded at 12 and 8 iterations, run imported, queue and verdict produced, 2 cases marked borderline.

## The splits

`make_splits.py` generates them deterministically and re-running it reproduces both files exactly.

| File | Cases | sha256 |
|---|---|---|
| `config-cases.txt` | 100 | `4f065572d826a133d2e300fbde1f352575ef33f2bce022f5bf7530775a67d08d` |
| `holdout-cases.txt` | 100 | `87e13bca14354a0270dba0def43eada789d59f40df6cb79ce4f744200cffc1ed` |

Seed **20260920**, the date the protocol was frozen, chosen so that it is transparently not the product of a search over seeds. Source cases are the 200 KITTI-2015 training pairs, `000000_10` through `000199_10`.

## Order of operations

The holdout half is not evaluated until the diagnostics are frozen and committed. That is the point of the split: the data does not exist to be peeked at.

### 1. Feasibility gate (protocol section 3)

```sh
rb verify-hook --checkpoint ckpt/raft-things.pth                 # reference build
# then again with mixed_precision = true in rb.toml, and with iterations = 8
rb verify-adapter --checkpoint ckpt/raft-things.pth              # adapter still agrees with RAFT's own path
```

Record, for each configuration: hook status, iterations fired, and the smallest update magnitude the driver reports. Gate item 3 is about FP16 underflow and the driver prints the number you need.

### 2. Configuration half

```sh
python3 build_compare.py --config rb.toml --checkpoint ckpt/raft-things.pth \
  --cases config-cases.txt --label mixed-precision \
  --reference iterations=12,mixed_precision=false \
  --build     iterations=12,mixed_precision=true \
  --out ref-vs-mixed.config.A.json

# same command again, --out ref-vs-mixed.config.B.json
#   the repeat gives the repeatability estimate for BOTH the reference and the
#   mixed-precision configuration, which protocol gate 4 requires separately

python3 build_compare.py --config rb.toml --checkpoint ckpt/raft-things.pth \
  --cases config-cases.txt --label truncated-8 \
  --reference iterations=12,mixed_precision=false \
  --build     iterations=8,mixed_precision=false \
  --out ref-vs-trunc.config.json
```

### 3. Freeze

Choose B1's selection threshold and the `[limits]` values from the configuration half **only**. Write them into `rb.toml`, fill in the remaining rows of the protocol's section 11 table, commit, push. Nothing below runs before that commit exists.

### 4. Held-out half

The same three commands with `--cases holdout-cases.txt` and `.holdout.` in the output names. Then `rb import` each, and analyse once.


### 5. Recorder ablation (amendment 2)

Pre-registered in section 12 of the protocol **before** it was run. Read that first: it states what the corrected channel is, what it does not license, and why the data underneath is not fresh.

`dual_recorder.py` records a second trajectory channel alongside the shipped one, in the same inference pass. The shipped channel is `delta_flow` at one eighth resolution, times 8. The corrected channel is the movement of the model's own full-resolution output, taken by wrapping `RAFT.upsample_flow`, unpadded, differenced in FP32 from a flow of exactly zero.

Check the machinery before running anything, on real weights at real KITTI geometry:

```sh
python3 verify_channels.py            # reference precision
python3 verify_channels.py --mixed    # only meaningful on a GPU; autocast is a no-op on CPU
```

It asserts four things and fails loudly on any of them: the output is bitwise unchanged by instrumentation, the corrected channel's last cumulative state is that output, the differences sum back to it, and each channel holds exactly one value per iteration.

Then add `--corrected` to any `build_compare.py` command in section 2 or 4 above. It writes a second comparison beside `--out` with `.corrected` in the name: same cases, same errors, same B1 tags, different trajectory. Import both and compare the rankings.

```sh
python3 build_compare.py --config rb.toml --checkpoint ckpt/raft-things.pth \
  --cases holdout-cases.txt --label truncated-8 --corrected \
  --reference iterations=12,mixed_precision=false \
  --build     iterations=8,mixed_precision=false \
  --out ref-vs-trunc.holdout.json
# -> ref-vs-trunc.holdout.json  and  ref-vs-trunc.holdout.corrected.json
```

Two things to keep straight. The coarse channel is recorded by the adapter itself, so the dual recorder borrows that recorder rather than attaching its own hook; attaching twice records every update twice and halves the apparent reversal rate. And `max_trajectory_regression` is in absolute trajectory units, which the corrected channel does not share, so it must be re-derived on the configuration half before any flag rate is compared.

## What the receipt does and does not pin

`rb import` records the source file by sha256, and the source file carries both build configurations, per-build hook status, device, seed, environment and the smallest update magnitude. The chain is auditable but indirect.

What it does **not** carry: `checkpoints.baseline.sha256` is null for imported runs, so the receipt cannot state by itself that both sides used the identical checkpoint. For this experiment that fact only lives in the input file's notes. **A replay-kit item, not a blocker:** a first-class deployment comparison should pin the checkpoint once and the two configurations beside it.

## Files

- `build_compare.py` - the driver
- `make_splits.py` - the split generator, deterministic
- `config-cases.txt`, `holdout-cases.txt` - the frozen splits
- `dual_recorder.py` - the corrected trajectory channel (amendment 2)
- `verify_channels.py` - its four correctness checks, on real weights
