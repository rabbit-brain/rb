"""Rabbit Brain: release review for iterative perception models.

Rank the cases that regressed or never settled, keep checks for the next checkpoint.
CLI: `rb`. Docs for agents and humans: `rb docs` (AGENTS.md).
"""
from __future__ import annotations

__version__ = "0.4.0"

from .recorder import TrajectoryRecorder  # noqa: E402
from .ledger import Ledger  # noqa: E402

__all__ = ["__version__", "TrajectoryRecorder", "Ledger"]
