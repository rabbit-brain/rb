"""Rabbit Brain: the research state for ML work done with coding agents.

Claims with criteria, settings with sources, evidence with receipts, in `.rb/` next to the code. Agents propose; rb checks
the sources and computes the verdicts; people decide. Release review for iterative perception models is built in.
CLI: `rb`. Docs for agents and humans: `rb docs` (AGENTS.md).
"""
from __future__ import annotations

__version__ = "0.5.0.dev0"

from .recorder import TrajectoryRecorder  # noqa: E402
from .errors import RBError  # noqa: E402
from .ledger import Ledger  # noqa: E402
from .sdk import open, init, State, Experiment, Claim, Run  # noqa: E402,A004

__all__ = ["__version__", "TrajectoryRecorder", "RBError", "Ledger", "open", "init", "State", "Experiment", "Claim", "Run"]
