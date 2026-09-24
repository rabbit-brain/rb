"""Research state: what a researcher is trying to establish, what would test it, what was run, and what the evidence
supports. `rb schema investigation` (and the other names below) print these as JSON Schema; `.rb/` holds one file per
object.

Two rules shape every object in this module.

1. **You and your agents propose; rb verifies; people decide.** Anyone may give a setting's value, and it is then
   `provisional`: it stands for now and has not been checked. It becomes `verified` only when `rb` itself resolves its source and finds the
   value stated there, and the resolution (commit, file hash, the line as read) is stored so a later change is
   detectable. A claim's verdict is never stored at all: it is computed from the evidence every time it is read.
2. **Unknown is a value.** A setting nobody has found is `unknown`, and every verdict on an experiment with an unknown or
   unverified setting names it. Nothing unknown reads as settled, which is the rule `rb check run` already applies to a
   limit that was declared and not evaluated.
"""
from __future__ import annotations

import math
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

LEDGER_VERSION = 1
ID_PATTERN = r"^[a-zA-Z0-9_.-]{1,80}$"             # the same id rule as models.py, restated because models.py imports this module
ACTOR_PATTERN = r"^(human|agent):[A-Za-z0-9_.@+-]{1,80}$"
NAME_PATTERN = r"^[A-Za-z0-9_.:/-]{1,80}$"          # setting and metric names: lr, optimizer.betas, cuda/version, candidate.mean_epe
TEXT = 1000

Scalar = Union[StrictBool, StrictInt, StrictFloat, StrictStr]
SettingValue = Optional[Union[Scalar, list[Scalar]]]

SettingStatus = Literal["unknown", "provisional", "verified", "imported"]
Comparator = Literal["within", "at_most", "at_least"]
ClaimOrigin = Literal["own", "source", "imported"]
VerdictStatus = Literal["not_tested", "supported", "refuted", "reproduced", "diverged", "contested", "imported"]


def _finite(v: float, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    return float(v)


# ---------------------------------------------------------------- sources


class Resolution(BaseModel):
    """What `rb spec verify` found when it read the source. Written by the tool only."""
    model_config = ConfigDict(extra="forbid")
    at: str
    commit: Optional[str] = None      # the commit the file was read at; None means an uncommitted working-tree file
    sha256: str                       # of the file as read, so a later change to it is detectable
    line: Optional[int] = None        # where the value was found
    text: str = Field(min_length=1, max_length=400)   # the line, or the JSON value, as read; re-checked against the value on every read


class Source(BaseModel):
    """Where a value or a claimed number comes from.

    `file`: a path (relative to the directory holding `.rb/`) with a line, a quote, or both, optionally at a commit. A
    paper's text saved as a file is a file source; `locator` says where a reader finds it ("§4.2", "Table 3, row 4").
    `run`: a JSON file (a run directory means its `record.json`) and a JSON pointer into it, e.g. `/seeds/torch`.
    `url` and `note` record where a value came from but cannot be checked mechanically, so a setting resting on one stays
    `provisional`."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "run", "url", "note"]
    path: Optional[str] = Field(default=None, min_length=1, max_length=400)
    line: Optional[int] = Field(default=None, ge=1)
    quote: Optional[str] = Field(default=None, min_length=1, max_length=400)
    commit: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{7,40}$")
    pointer: Optional[str] = Field(default=None, max_length=200)
    url: Optional[str] = Field(default=None, max_length=600)
    locator: Optional[str] = Field(default=None, max_length=120)
    note: str = Field(default="", max_length=400)
    resolved: Optional[Resolution] = None

    @model_validator(mode="after")
    def _shape(self) -> "Source":
        if self.kind == "file" and not self.path:
            raise ValueError("a file source needs a path")
        if self.kind == "run":
            if not self.path or self.pointer is None:
                raise ValueError("a run source needs a path and a JSON pointer, e.g. run:rb-runs/<id>/record.json#/seeds/torch")
            if self.pointer and not self.pointer.startswith("/"):
                raise ValueError("a JSON pointer starts with '/', e.g. /seeds/torch")
        if self.kind == "url" and not self.url:
            raise ValueError("a url source needs the url")
        if self.kind == "note" and not self.note:
            raise ValueError("a note source needs the note")
        if self.resolved is not None and not self.verifiable():
            raise ValueError("only a file source with a line or quote, or a run source, can be resolved")
        return self

    def verifiable(self) -> bool:
        return self.kind == "run" or (self.kind == "file" and (self.line is not None or bool(self.quote)))

    def label(self) -> str:
        if self.kind == "file":
            s = f"{self.path}:{self.line}" if self.line else str(self.path)
            if self.quote:
                s += f' "{self.quote if len(self.quote) <= 60 else self.quote[:57] + "..."}"'
            if self.commit:
                s += f" @{self.commit[:7]}"
        elif self.kind == "run":
            s = f"{self.path}#{self.pointer}"
        elif self.kind == "url":
            s = str(self.url)
        else:
            s = f"note: {self.note}"
        return f"{s} ({self.locator})" if self.locator else s


# ---------------------------------------------------------------- settings and experiments


class Setting(BaseModel):
    """One setting an experiment depends on: a learning rate, a seed, a CUDA version, a preprocessing choice, a checkpoint.
    `required` settings that are unknown block the experiment's claims from being read as settled; an optional one is
    recorded and reported but does not block."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=NAME_PATTERN)
    value: SettingValue = None
    status: SettingStatus = "unknown"
    required: bool = True
    source: Optional[Source] = None
    note: str = Field(default="", max_length=400)
    set_by: str = Field(pattern=ACTOR_PATTERN)
    set_at: str

    @field_validator("value")
    @classmethod
    def _no_blank_text(cls, v: SettingValue) -> SettingValue:
        items = v if isinstance(v, list) else [v]
        if isinstance(v, list) and not v:
            raise ValueError("an empty list is not a value; record a setting nobody has found as unknown")
        for x in items:
            if isinstance(x, str) and not x.strip():
                raise ValueError("empty text is not a value; record a setting nobody has found as unknown")
            if isinstance(x, float) and not math.isfinite(x):
                raise ValueError("a value must be a finite number")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Setting":
        if self.status == "unknown" and self.value is not None:
            raise ValueError(f"setting {self.name}: an unknown setting has no value")
        if self.status in ("provisional", "verified", "imported") and self.value is None:
            raise ValueError(f"setting {self.name}: {self.status} without a value; a setting nobody has found is unknown")
        if self.status == "verified" and (self.source is None or self.source.resolved is None):
            raise ValueError(f"setting {self.name}: verified without a resolved source. Only `rb spec verify` sets verified, and it stores what it read")
        return self


class Freeze(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)


class Amendment(BaseModel):
    """A change to a frozen experiment's spec: what changed, why, and the spec hash before and after."""
    model_config = ConfigDict(extra="forbid")
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    reason: str = Field(min_length=1, max_length=TEXT)
    change: str = Field(max_length=400)
    before: str
    after: str


class Experiment(BaseModel):
    """What would test a hypothesis: the baseline, the candidate and the settings. Its claims point at it; its evidence is
    attached to it. Once frozen, a change to the spec (a setting's value, a claim's criterion) needs a stated reason and is
    kept as an amendment, which is pre-registration as a command."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    tests: list[str] = Field(default_factory=list)       # hypothesis ids
    baseline: Optional[str] = Field(default=None, max_length=200)
    candidate: Optional[str] = Field(default=None, max_length=200)
    settings: list[Setting] = Field(default_factory=list)
    frozen: Optional[Freeze] = None
    amendments: list[Amendment] = Field(default_factory=list)
    note: str = Field(default="", max_length=TEXT)
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str

    @field_validator("settings")
    @classmethod
    def _unique(cls, v: list[Setting]) -> list[Setting]:
        names = [k.name for k in v]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate setting names: {', '.join(dup)}")
        return v

    def setting(self, name: str) -> Optional[Setting]:
        return next((k for k in self.settings if k.name == name), None)


# ---------------------------------------------------------------- claims and evidence


class Claim(BaseModel):
    """A statement with a criterion that evidence can meet or miss.

    `origin`: `own` is a prediction or threshold the researcher sets ("INT8 keeps mean EPE within 0.05 px"); `source` is
    a number someone else stated, a paper's table cell, which the evidence reproduces or not; `imported` came from a
    parent investigation and stays imported until this investigation attaches its own evidence."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    statement: str = Field(min_length=1, max_length=400)
    experiment: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    hypothesis: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    metric: str = Field(pattern=NAME_PATTERN)
    comparator: Comparator
    target: float
    tolerance: Optional[float] = Field(default=None, ge=0)
    origin: ClaimOrigin = "own"
    source: Optional[Source] = None
    note: str = Field(default="", max_length=TEXT)
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str

    @field_validator("target")
    @classmethod
    def _target(cls, v: float) -> float:
        return _finite(v, "target")

    @field_validator("tolerance")
    @classmethod
    def _tolerance(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None else _finite(v, "tolerance")

    @model_validator(mode="after")
    def _criterion(self) -> "Claim":
        if self.comparator == "within" and self.tolerance is None:
            raise ValueError("a 'within' claim needs a tolerance")
        if self.comparator != "within" and self.tolerance is not None:
            raise ValueError("a tolerance applies only to a 'within' claim; at_most and at_least compare against the target itself")
        if self.origin == "source" and self.source is None:
            raise ValueError("a claim whose number comes from a source needs that source")
        return self

    def criterion(self) -> str:
        if self.comparator == "within":
            return f"{self.metric} within {self.target:g} ± {self.tolerance:g}"
        return f"{self.metric} {'≤' if self.comparator == 'at_most' else '≥'} {self.target:g}"


class FileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    bytes: int


class Retraction(BaseModel):
    """Evidence is never deleted. Evidence found to be wrong (a pipeline bug, the wrong checkpoint) is retracted with a
    reason: it stays on record and stops counting toward any verdict."""
    model_config = ConfigDict(extra="forbid")
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    reason: str = Field(min_length=1, max_length=TEXT)


class Receipt(BaseModel):
    """What the tool recorded when the evidence was attached. `command` is what the caller said produced the numbers;
    rb did not run it. For an `rb run` or `rb import`, `run_record` carries the run's own receipt fields and the hash of
    its record.json."""
    model_config = ConfigDict(extra="allow")
    at: str
    actor: str = Field(pattern=ACTOR_PATTERN)
    rb_version: str
    command: Optional[str] = None
    git: Optional[dict] = None
    environment: dict = Field(default_factory=dict)
    run_record: Optional[dict] = None


class Evidence(BaseModel):
    """Numbers produced by a run, attached to the experiment they test, with the receipt that says where they came from."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    experiment: str = Field(pattern=ID_PATTERN)
    kind: Literal["rb_run", "observation"]
    metrics: dict[str, float] = Field(default_factory=dict)
    run: Optional[str] = None
    files: list[FileRef] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
    spec_sha256: str                      # the experiment's whole spec when this was attached
    setup_sha256: Optional[str] = None    # the part of it the evidence depends on: baseline, candidate, settings (not the claims)
    spec_frozen: bool                     # whether the experiment was frozen at that moment
    synthetic: bool = False               # from rb's built-in example or demo data, which is not evidence about any model
    receipt: Receipt
    note: str = Field(default="", max_length=TEXT)
    retracted: Optional[Retraction] = None

    @field_validator("metrics")
    @classmethod
    def _metrics(cls, v: dict[str, float]) -> dict[str, float]:
        import re
        for k, x in v.items():
            if not re.fullmatch(NAME_PATTERN, k):
                raise ValueError(f"metric names are letters, digits and _.:/- (got {k!r})")
            _finite(x, f"metric {k}")
        return {k: float(x) for k, x in v.items()}


# ---------------------------------------------------------------- the research questions


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT)
    status: Literal["open", "answered", "dropped"] = "open"
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str


class Hypothesis(BaseModel):
    """What you expect and why. `accepted` and `rejected` are set by a person's decision, never by a verdict alone."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    statement: str = Field(min_length=1, max_length=TEXT)
    question: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    expect: str = Field(default="", max_length=TEXT)
    why: str = Field(default="", max_length=TEXT)
    status: Literal["proposed", "active", "accepted", "rejected"] = "proposed"
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str


class Assumption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT)
    applies_to: list[str] = Field(default_factory=list)   # experiment ids
    status: Literal["open", "holds", "violated"] = "open"
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str


class Decision(BaseModel):
    """A person's call on a claim, hypothesis, assumption, question or experiment, with the reasoning and with the verdict
    the claim had at that moment, so a decision is always read against what it was made on."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    subject: str = Field(pattern=ID_PATTERN)
    outcome: Literal["accept", "reject", "investigate"]
    why: str = Field(min_length=1, max_length=TEXT)
    by: str = Field(pattern=r"^human:[A-Za-z0-9_.@+-]{1,80}$")
    at: str
    verdict: Optional[VerdictStatus] = None
    evidence: list[str] = Field(default_factory=list)


class Origin(BaseModel):
    """Where a cloned investigation came from. Everything it brought with it is `imported` until reproduced here."""
    model_config = ConfigDict(extra="forbid")
    url: str
    version: Optional[str] = None
    cloned_at: str


class Investigation(BaseModel):
    """`.rb/investigation.json`: the root of the research state for one line of work."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = LEDGER_VERSION
    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str
    origin: Optional[Origin] = None


# ---------------------------------------------------------------- verdicts (computed, never stored)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: str
    value: float
    holds: bool
    margin: float                 # how far inside (positive) or outside (negative) the criterion
    borderline: bool              # moving the criterion by a tenth would change the call
    spec: Literal["current", "changed_since", "amended_since", "before_freeze"]   # the setup the evidence was produced under, against the experiment's now
    post_hoc: bool = False        # the claim was written after this evidence was attached


class ClaimVerdict(BaseModel):
    """`rb show <claim>` and `rb status`: the claim's standing, computed from the evidence now."""
    model_config = ConfigDict(extra="forbid")
    claim: str
    statement: str
    criterion: str
    origin: ClaimOrigin
    status: VerdictStatus
    established: bool = False       # supported or reproduced, with no required setting unknown, no stale source and not only example data
    observations: list[Observation] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)    # what the verdict rests on that is not established
    blocking: list[str] = Field(default_factory=list)      # required settings still unknown
    without_metric: list[str] = Field(default_factory=list)   # evidence on the experiment that does not report this metric
    retracted: list[str] = Field(default_factory=list)
    decision: Optional[str] = None                         # the latest decision on this claim, if any


OBJECT_SCHEMAS: dict[str, type[BaseModel]] = {
    "investigation": Investigation,
    "question": Question,
    "hypothesis": Hypothesis,
    "assumption": Assumption,
    "experiment": Experiment,
    "claim": Claim,
    "evidence": Evidence,
    "decision": Decision,
    "verdict": ClaimVerdict,
}
