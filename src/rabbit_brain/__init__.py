"""Rabbit Brain: the record of the experiments your coding agents run, kept as plain files in your repo.

Experiments with variants and criteria, runs with receipts, and the datasets and checkpoints they used, recorded as YAML
in `.rb/` beside the code. Agents propose and record; rb checks sources and computes findings; you decide. CLI: `rb`.
Documentation: `rb docs` (AGENTS.md).
"""
from __future__ import annotations

import os
from typing import Any

__version__ = "0.6.1"
if os.environ.get("RB_TEST_VERSION"):      # the tests pin the version, as RB_TEST_NOW pins the clock, so what they expect outlives a release
    __version__ = os.environ["RB_TEST_VERSION"]

from .errors import RBError, RBWarning  # noqa: E402

_LAZY = {
    "open": ".sdk", "init": ".sdk", "clone": ".sdk", "State": ".sdk", "Experiment": ".sdk", "Criterion": ".sdk", "Run": ".sdk",
    "Spec": ".sdk", "DryRun": ".sdk",
}

__all__ = ["__version__", "RBError", "RBWarning", *_LAZY]


def __getattr__(name: str) -> Any:
    """Loaded on first use, so `import rabbit_brain` (and every `rb` command) does not pay for what it does not touch."""
    if name in _LAZY:
        import importlib
        value = getattr(importlib.import_module(_LAZY[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'rabbit_brain' has no attribute {name!r}")
