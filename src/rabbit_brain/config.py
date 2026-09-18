"""`rb.toml`: one project's runner configuration. Written by `rb init`, read by `rb doctor`, `rb verify-hook` and `rb run`."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import RBError
from .models import Limits, Metric

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_NAME = "rb.toml"


class ProjectSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    task: str = "flow"


class AdapterSection(BaseModel):
    """`id` names a built-in adapter (raft, synthetic); `module` names a custom one as `package.module:Class`."""
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    module: Optional[str] = None
    model_code: Optional[str] = None
    iterations: int = Field(12, ge=2, le=64)
    device: str = "cuda"
    small: bool = False
    mixed_precision: bool = False
    alternate_corr: bool = False


class DatasetSection(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(min_length=1, max_length=120)
    kind: str = "kitti"
    path: Optional[str] = None
    cases: str = "all"   # "all", a path to a file with one case id per line, or an integer limit as text


class EvidenceSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["none", "standard", "full"] = "none"
    top: int = Field(20, ge=0, le=500)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: ProjectSection
    adapter: AdapterSection
    dataset: DatasetSection
    limits: Limits = Field(default_factory=Limits)
    metric: Optional[Metric] = None
    evidence: EvidenceSection = Field(default_factory=EvidenceSection)


def load_config(path: Path = Path(CONFIG_NAME)) -> Config:
    if not path.exists():
        raise RBError("E_CONFIG_MISSING", message=f"No {path} in {Path.cwd()}.")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise RBError("E_CONFIG_INVALID", message=f"{path}: {exc}")
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        raise RBError("E_CONFIG_INVALID", message=f"{path}: {loc}: {first.get('msg', 'invalid')}")


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config(cfg: Config) -> str:
    a, d, lim, e = cfg.adapter, cfg.dataset, cfg.limits, cfg.evidence
    lines = [
        "# Rabbit Brain runner configuration. Docs: rb docs",
        "[project]",
        f"name = {_toml_str(cfg.project.name)}",
        f"task = {_toml_str(cfg.project.task)}",
        "",
        "[adapter]",
    ]
    if a.id:
        lines.append(f"id = {_toml_str(a.id)}                     # built-in: raft | synthetic")
    if a.module:
        lines.append(f"module = {_toml_str(a.module)}          # custom adapter as package.module:Class")
    if a.model_code:
        lines.append(f"model_code = {_toml_str(a.model_code)}        # the model repository (RAFT: its core/ is put on sys.path)")
    lines += [
        f"iterations = {a.iterations}                  # refinement iterations per case; the trajectory has this many values",
        f"device = {_toml_str(a.device)}",
        f"small = {'true' if a.small else 'false'}                     # RAFT: random-weight checks only; real checkpoints are read as raft or raft-small from their keys",
        f"mixed_precision = {'true' if a.mixed_precision else 'false'}",
        "",
        "[dataset]",
        f"name = {_toml_str(d.name)}",
        f"kind = {_toml_str(d.kind)}",
    ]
    if d.path:
        lines.append(f"path = {_toml_str(d.path)}")
    lines += [
        f"cases = {_toml_str(d.cases)}                     # \"all\", a file with one case id per line, or a number (first N)",
        "",
        "[limits]",
        f"max_regression = {lim.max_regression}             # in the metric's unit",
        f"max_late_share = {lim.max_late_share}",
        f"max_reversals = {lim.max_reversals}",
        (f"max_last_update = {lim.max_last_update}" if lim.max_last_update is not None else "# max_last_update = 0.3           # off unless set: a final update larger than this (trajectory unit) = not settled"),
        "",
        "[evidence]",
        f"level = {_toml_str(e.level)}                    # none | standard | full (rendering arrives in 0.2)",
        f"top = {e.top}",
        "",
    ]
    return "\n".join(lines)


GITIGNORE_SNIPPET = "# Rabbit Brain: evidence images are large; the JSON receipts and report are small and worth committing\nrb-runs/*/evidence/\n"
