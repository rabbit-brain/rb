"""The envelope every command prints with --json, and the registry `rb schema` prints from."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ErrorItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    code: str
    message: str
    fix: Optional[str] = None


class Envelope(BaseModel):
    """What every command prints with --json: exactly one object on stdout."""
    model_config = ConfigDict(extra="forbid")
    ok: bool
    rb_version: str
    command: str
    data: dict = Field(default_factory=dict)
    errors: list[ErrorItem] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)
    rb_delta: list[dict] = Field(default_factory=list)   # in an agent session: what changed in the research since it last looked


def _schemas() -> dict[str, type[BaseModel]]:
    from .record import OBJECT_SCHEMAS
    return {**OBJECT_SCHEMAS, "envelope": Envelope}


SCHEMAS: dict[str, type[BaseModel]] = _schemas()
