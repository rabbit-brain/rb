"""A repository an agent left after an afternoon of research, shaped the way coding agents write them: result folders
with a README, a frozen manifest of source hashes, an analysis and an audit file, per-case outputs and large arrays kept
out of git; research code with audit scripts and tests; a reproduction guide with its commands; one git commit.

Every name and number here is synthetic."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")       # the same bytes on every system, Windows too
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


CODE = {
    "research/warmup/__init__.py": "",
    "research/warmup/run_confirmation.py": "def main():\n    return 0\n",
    "research/warmup/analyze_confirmation.py": "def check(directory):\n    assert directory\n",
    "research/warmup/run_transfer.py": "def main():\n    return 0\n",
    "research/warmup/audit_transfer.py": "def check(directory):\n    assert directory\n    return True\n",
    "research/warmup/test_transfer.py": "def test_transfer():\n    assert 1 + 1 == 2\n",
    "research/warmup/transfer_protocol.md": "# Transfer protocol\n\nThe warmup arm is compared with cosine_only on the wider model, 8 widths.\n",
}

REPRODUCTION = """# Warmup study: how to rerun it

Written 2026-09-30 for anyone starting from a clean clone.

Git holds the code, the protocol, the tests and every per-seed JSON file. The model checkpoints
stay on the lab disk and are ignored by git.

Reports: [confirmation](../../results/warmup_confirmation/README.md) and [transfer](../../results/warmup_transfer/README.md).
Confirmation clears its gate; transfer does not beat cosine_only.

## Environment and tests

Tested on Linux with Python 3.12 and NumPy 1.26, one BLAS thread.

```
python -m pytest research/warmup/test_transfer.py
```

## Rerun and audit

Write to a fresh folder so the committed results stay as they are:

```
env OMP_NUM_THREADS=1 \\
  python -m research.warmup.run_confirmation \\
  --output results/NEW_confirmation
python -m research.warmup.analyze_confirmation --result results/NEW_confirmation
```

The transfer results come with an audit script:

```
python -c 'from research.warmup.audit_transfer import check; check("results/warmup_transfer")'
```
"""

CONFIRMATION_README = """# Confirmation on 12 seeds

All six schedules converge and reach the target loss on the 12 seeds.

## Result

The warmup arm passes its frozen gate on 12 of 12 seeds: 2.5% fewer steps than no_warmup.

## Limitations

The 12 original checkpoints stay local; the commands in the guide produce a new run.

For a clean clone, read the [rerun guide](../../research/warmup/reproduction.md).
"""

TRANSFER_README = """# Transfer to the wider model

The warmup arm is compared with cosine_only on 8 model widths, under the [protocol](../../research/warmup/transfer_protocol.md).

## Result

The warmup arm does not beat cosine_only: 8.8% more steps on the wider model.

## Next

Remove warmup at recorded checkpoints and count the steps left to the target loss.

For a clean clone, read the [rerun guide](../../research/warmup/reproduction.md).
"""

COST_README = """# Compute and accounting audit

Accounting of compute per warmup step; not yet summarised.
"""


def build(root: Path, commit: bool = True) -> Path:
    """Write the repository into `root` (a fresh directory), commit it, and return root."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, text in CODE.items():
        _write(root, rel, text)
    _write(root, "research/warmup/reproduction.md", REPRODUCTION)
    _write(root, "results/.gitignore", "# checkpoints stay on the lab disk; metrics and manifests stay in git\n*.npz\n")
    for name, readme, gate in (("warmup_confirmation", CONFIRMATION_README, True), ("warmup_transfer", TRANSFER_README, False)):
        folder = f"results/{name}"
        _write(root, f"{folder}/README.md", readme)
        _write(root, f"{folder}/analysis.json", json.dumps({"gate": {"passed": gate}, "arms": {"warmup": {"steps": 0.9 if gate else 1.09},
                                                                                              "baseline": {"steps": 1.0}}}, indent=1))
        for case in ("a", "b"):
            _write(root, f"{folder}/case_{case}.json", json.dumps({"case": case, "steps": 10, "converged": True}))
            (root / folder / f"case_{case}.npz").write_bytes(b"\x93NUMPY" + bytes(64))
        sources = {rel: _sha(root / rel) for rel in ("research/warmup/run_confirmation.py", "research/warmup/analyze_confirmation.py",
                                                     "research/warmup/run_transfer.py", "research/warmup/audit_transfer.py")}
        _write(root, f"{folder}/manifest.json", json.dumps({"frozen_utc": "2026-09-30T18:20:34+00:00", "sources": sources}, indent=1))
    _write(root, "results/warmup_transfer/independent_audit.json", json.dumps({"passed": True, "checks": 12}))
    _write(root, "results/warmup_cost/README.md", COST_README)
    _write(root, "results/warmup_cost/summary.json", json.dumps({"widths": 4, "cost": 0.002}))
    if commit:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "checkpoint the warmup study"], check=True)
    return root
