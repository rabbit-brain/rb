"""Research state: what a line of work is trying to establish, what would test it, what was run, and what the evidence
supports. `.rb/` holds one JSON file per object; `rb schema <kind>` prints each shape.

The ontology is closed on purpose. A new result is attached to a known experiment and variant, named by a known metric,
and judged by a known claim, so that recording work (by a person, an agent, or later a normalizer) is choosing among
known things rather than inventing structure:

    Investigation
      Question -> Hypothesis -> Claim (criterion) <- judged on -> Evidence (numbers + receipt)
      Experiment: shared settings + Variants (baseline, candidate, control, ablation), each with its own settings;
                  `varies` names the Variables it changes on purpose
      Metric (catalogue): unit, direction, aliases
      Decision: a person's call, with the verdict it was made on

Three rules shape every object here.

1. **You and your agents propose; rb verifies; people decide.** A setting anyone gives is `provisional`. It becomes
   `verified` only when rb reads its source and finds the value stated there, and what rb read is stored and re-checked.
   A claim's verdict is never stored: it is computed from the evidence on every read.
2. **Unknown is a value.** A setting nobody has found is `unknown`, and a claim on its experiment is not established.
3. **Nothing is deleted.** A mistake is retracted with a reason, stays on record, and stops counting.
"""
from __future__ import annotations

import math
import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

LEDGER_VERSION = 1
ID_PATTERN = r"^[a-zA-Z0-9_.-]{1,80}$"                  # object ids; no '/', so e1/lr addresses a setting unambiguously
ACTOR_PATTERN = r"^(human|agent):[A-Za-z0-9_.@+-]{1,80}$"
PERSON_PATTERN = r"^human:[A-Za-z0-9_.@+-]{1,80}$"
SETTING_PATTERN = r"^[A-Za-z0-9_.:-]{1,80}$"             # lr, optim.lr; a variant's own setting is <variant>.<name>
VARIANT_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"               # no '.', so int8.lr splits into variant and setting
METRIC_PATTERN = r"^[A-Za-z0-9_.:/-]{1,80}$"             # val/loss is allowed: W&B names metrics that way
TEXT = 1000

Scalar = Union[StrictBool, StrictInt, StrictFloat, StrictStr]
SettingValue = Optional[Union[Scalar, list[Scalar]]]

Via = Literal["cli", "sdk", "mcp", "api"]
SettingStatus = Literal["unknown", "provisional", "verified", "inherited"]
Role = Literal["baseline", "candidate", "control", "ablation"]
Comparator = Literal["at_most", "at_least", "equals"]
ClaimOrigin = Literal["own", "cited", "inherited"]
Direction = Literal["minimize", "maximize", "none"]
VerdictStatus = Literal["untested", "supported", "refuted", "mixed", "reproduced", "not_reproduced", "not_comparable", "inherited"]
ObservationRole = Literal["confirmatory", "exploratory", "not_counted"]
Basis = Literal["run", "file", "logged", "typed"]    # logged: reported through the SDK by the code that produced it


def _finite(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    return float(v)


class Retraction(BaseModel):
    """A mistake is retracted, never deleted: it stays on record with who and why, and stops counting."""
    model_config = ConfigDict(extra="forbid")
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    why: str = Field(min_length=1, max_length=TEXT)


class Written(BaseModel):
    """Who wrote an object, when, and through which surface."""
    model_config = ConfigDict(extra="forbid")
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str
    via: Via = "cli"
    retracted: Optional[Retraction] = None


# ---------------------------------------------------------------- sources


class Resolution(BaseModel):
    """What rb read when it verified a source. Written by rb only, and re-checked on every read."""
    model_config = ConfigDict(extra="forbid")
    at: str
    commit: Optional[str] = None      # the commit the file was read at; None for an uncommitted working-tree file
    sha256: str                       # of the file as read
    line: Optional[int] = None
    text: str = Field(min_length=1, max_length=400)   # the line, or the value, as read
    read: Any = None                  # the value found at a key path or JSON pointer


class Source(BaseModel):
    """Where a value or a cited number comes from.

    `file`: a path (relative to the directory holding .rb/) with a `key` path for YAML, JSON or TOML (`optim.lr`), or a
    `line`, or a `quote`; optionally read at a `commit`. A paper saved as text is a file source; `locator` says where a
    reader finds it ("§4.2", "Table 3") and `term` is the word a prose line uses for the setting ("learning rate").
    `run`: a JSON file (a run directory means its record.json) and a JSON `pointer`. `url` and `note` record where a value
    came from but cannot be checked by reading a file."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "run", "url", "note"]
    path: Optional[str] = Field(default=None, min_length=1, max_length=400)
    key: Optional[str] = Field(default=None, min_length=1, max_length=200)
    line: Optional[int] = Field(default=None, ge=1)
    quote: Optional[str] = Field(default=None, min_length=1, max_length=400)
    term: Optional[str] = Field(default=None, min_length=1, max_length=80)
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
                raise ValueError("a run source needs a path and a JSON pointer, e.g. run:rb-runs/<id>#/seeds/torch")
            if self.pointer and not self.pointer.startswith("/"):
                raise ValueError("a JSON pointer starts with '/', e.g. /seeds/torch")
        if self.kind == "url" and not self.url:
            raise ValueError("a url source needs the url")
        if self.kind == "note" and not self.note:
            raise ValueError("a note source needs the note")
        if self.resolved is not None and not self.checkable():
            raise ValueError("only a file source with a key, line or quote, or a run source, can be resolved")
        return self

    def checkable(self) -> bool:
        return self.kind == "run" or (self.kind == "file" and (self.key is not None or self.line is not None or bool(self.quote)))

    def label(self) -> str:
        if self.kind == "file":
            s = str(self.path)
            if self.key:
                s += f"#{self.key}"
            elif self.line:
                s += f":{self.line}"
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


# ---------------------------------------------------------------- settings, variants, experiments


class Cited(BaseModel):
    """The value a cited source (a paper, the authors' evaluation code) used for this setting. When it differs from ours,
    a cited claim on the experiment is not comparable rather than not reproduced."""
    model_config = ConfigDict(extra="forbid")
    value: SettingValue
    source: Optional[Source] = None


class Conflict(BaseModel):
    """A verified source that now states a different value. Re-verifying never clears it: the setting's value or its
    source has to change."""
    model_config = ConfigDict(extra="forbid")
    at: str
    read: Any = None
    text: str = Field(default="", max_length=400)


class Setting(BaseModel):
    """One thing an experiment depends on: a learning rate, a seed, a CUDA version, a preprocessing choice, a checkpoint.
    A required setting that is unknown, or provisional with no person vouching for it, keeps the experiment's claims from
    being established. `per_run` settings (a seed) are given by each piece of evidence rather than fixed in the spec."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=SETTING_PATTERN)
    value: SettingValue = None
    status: SettingStatus = "unknown"
    required: bool = True
    per_run: bool = False
    source: Optional[Source] = None
    cited: Optional[Cited] = None
    conflict: Optional[Conflict] = None
    note: str = Field(default="", max_length=400)
    set_by: str = Field(pattern=ACTOR_PATTERN)
    set_at: str
    via: Via = "cli"
    retracted: Optional[Retraction] = None

    @field_validator("value")
    @classmethod
    def _no_blank(cls, v: SettingValue) -> SettingValue:
        if isinstance(v, list) and not v:
            raise ValueError("an empty list is not a value; record a setting nobody has found as unknown")
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, str) and not x.strip():
                raise ValueError("empty text is not a value; record a setting nobody has found as unknown")
            if isinstance(x, float) and not math.isfinite(x):
                raise ValueError("a value must be a finite number")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Setting":
        if self.per_run:
            if self.value is not None or self.status != "unknown":
                raise ValueError(f"setting {self.name}: a per-run setting has no single value; each piece of evidence gives its own")
            return self
        if self.status == "unknown" and self.value is not None:
            raise ValueError(f"setting {self.name}: an unknown setting has no value")
        if self.status != "unknown" and self.value is None:
            raise ValueError(f"setting {self.name}: {self.status} without a value; a setting nobody has found is unknown")
        if self.status == "verified" and (self.source is None or self.source.resolved is None):
            raise ValueError(f"setting {self.name}: verified without what rb read. Only `rb spec verify` sets verified")
        return self


class Variant(BaseModel):
    """One arm of an experiment: the baseline, a candidate, a control or an ablation. Its settings override the
    experiment's shared ones; a setting it does not name is shared."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=VARIANT_PATTERN)
    role: Role
    settings: list[Setting] = Field(default_factory=list)
    note: str = Field(default="", max_length=TEXT)
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str
    via: Via = "cli"
    retracted: Optional[Retraction] = None

    def setting(self, name: str) -> Optional[Setting]:
        return next((s for s in self.settings if s.name == name and s.retracted is None), None)


class Freeze(BaseModel):
    """Pre-registration: the spec and the claim criteria locked by a person before the evidence that counts."""
    model_config = ConfigDict(extra="forbid")
    sha256: str
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    why: str = Field(default="", max_length=TEXT)
    after_evidence: list[str] = Field(default_factory=list)   # evidence already attached when it was frozen: exploratory, never confirmatory


class Amendment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    why: str = Field(min_length=1, max_length=TEXT)
    change: str = Field(max_length=400)
    before: str
    after: str


class Experiment(Written):
    """What would test a hypothesis: shared settings, the variants it compares, and the settings it varies on purpose
    (`varies`). A setting that differs between variants without being declared in `varies` is a confound."""
    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    hypotheses: list[str] = Field(default_factory=list)
    varies: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    settings: list[Setting] = Field(default_factory=list)
    frozen: Optional[Freeze] = None
    amendments: list[Amendment] = Field(default_factory=list)
    note: str = Field(default="", max_length=TEXT)

    @field_validator("variants")
    @classmethod
    def _unique_variants(cls, v: list[Variant]) -> list[Variant]:
        names = [x.name for x in v]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate variant names: {', '.join(dup)}")
        return v

    @field_validator("settings")
    @classmethod
    def _unique_settings(cls, v: list[Setting]) -> list[Setting]:
        names = [s.name for s in v]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate setting names: {', '.join(dup)}")
        return v

    def variant(self, name: str) -> Optional[Variant]:
        return next((v for v in self.variants if v.name == name and v.retracted is None), None)

    def live_variants(self) -> list[Variant]:
        return [v for v in self.variants if v.retracted is None]

    def by_role(self, role: str) -> list[Variant]:
        return [v for v in self.live_variants() if v.role == role]

    def setting(self, name: str) -> Optional[Setting]:
        return next((s for s in self.settings if s.name == name and s.retracted is None), None)

    def split(self, name: str) -> tuple[Optional[Variant], str]:
        """`int8.lr` is variant int8's own lr when int8 is a variant of this experiment; anything else is shared."""
        head, sep, tail = name.partition(".")
        if sep and tail:
            v = self.variant(head)
            if v is not None:
                return v, tail
        return None, name

    def lookup(self, name: str) -> Optional[Setting]:
        variant, bare = self.split(name)
        return variant.setting(bare) if variant is not None else self.setting(bare)

    def all_settings(self) -> list[tuple[str, Setting]]:
        """Every live setting with its full name: shared ones as themselves, a variant's as <variant>.<name>."""
        out = [(s.name, s) for s in self.settings if s.retracted is None]
        for v in self.live_variants():
            out += [(f"{v.name}.{s.name}", s) for s in v.settings if s.retracted is None]
        return out

    def value_in(self, variant: Variant, name: str) -> SettingValue:
        own = variant.setting(name)
        if own is not None:
            return own.value
        shared = self.setting(name)
        return shared.value if shared is not None else None


# ---------------------------------------------------------------- catalogue


class Metric(Written):
    """A measurement the catalogue knows: its unit, which direction is better, and the other names it goes by. Evidence
    may report a metric that is not declared; it is shown as undeclared rather than dropped."""
    id: str = Field(pattern=ID_PATTERN)
    unit: str = Field(default="", max_length=16)
    direction: Direction = "none"
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=TEXT)

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        for a in v:
            if not re.fullmatch(METRIC_PATTERN, a):
                raise ValueError(f"alias {a!r}: letters, digits and _.:/- only")
        return v


# ---------------------------------------------------------------- claims and evidence


class Claim(Written):
    """A statement with a criterion that evidence can meet or miss, fixed when written.

    `metric` names what the evidence reports: `epe` (the only candidate's, when there is one), `int8.epe` (a variant's),
    `candidate.epe` / `baseline.epe` (by role), or `change.epe` (candidate minus baseline, from one piece of evidence that
    reports both). `over` says whether each run must meet the criterion or their mean; `min_n` how many confirmatory runs
    it takes; `noise` the run-to-run spread under which a result counts as borderline.

    `origin`: `own` is a criterion you set; `cited` is a number someone else stated (a paper's table), which the evidence
    reproduces or not; `inherited` came from a parent investigation and stays so until reproduced here."""
    id: str = Field(pattern=ID_PATTERN)
    statement: str = Field(min_length=1, max_length=400)
    experiment: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    hypothesis: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    metric: str = Field(pattern=METRIC_PATTERN)
    comparator: Comparator
    target: float
    tolerance: Optional[float] = Field(default=None, ge=0)
    over: Literal["each", "mean"] = "each"
    min_n: int = Field(default=1, ge=1, le=1000)
    noise: Optional[float] = Field(default=None, ge=0)
    origin: ClaimOrigin = "own"
    source: Optional[Source] = None
    note: str = Field(default="", max_length=TEXT)

    @field_validator("target")
    @classmethod
    def _target(cls, v: float) -> float:
        return _finite(v, "target")

    @field_validator("tolerance", "noise")
    @classmethod
    def _optional_finite(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None else _finite(v, "value")

    @model_validator(mode="after")
    def _criterion(self) -> "Claim":
        if self.comparator == "equals" and self.tolerance is None:
            raise ValueError("an 'equals' claim needs a tolerance")
        if self.comparator != "equals" and self.tolerance is not None:
            raise ValueError("a tolerance applies only to an 'equals' claim")
        if self.origin == "cited" and self.source is None:
            raise ValueError("a cited claim needs its source")
        return self

    def criterion(self) -> str:
        """The whole criterion as a person reads it before freezing: the rule, and how many runs and what noise it takes."""
        head = f"mean {self.metric}" if self.over == "mean" else self.metric
        rule = f"{head} = {self.target:g} ± {self.tolerance:g}" if self.comparator == "equals" else f"{head} {'≤' if self.comparator == 'at_most' else '≥'} {self.target:g}"
        extra = ([f"n ≥ {self.min_n}"] if self.min_n > 1 else []) + ([f"noise {self.noise:g}"] if self.noise is not None else [])
        return rule + (f" ({', '.join(extra)})" if extra else "")


class FileRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    bytes: int


class ConfigCheck(BaseModel):
    """The run's own resolved config (Hydra, W&B, Lightning, an rb run's record) compared with the spec: whether the run
    actually used the settings the spec says, not only whether a file states them."""
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    matches: list[str] = Field(default_factory=list)
    mismatches: list[dict] = Field(default_factory=list)    # {name, spec, ran}
    absent: list[str] = Field(default_factory=list)          # spec settings the config does not mention


class Receipt(BaseModel):
    """What rb recorded when the evidence was attached. `attached` is the repository at that moment; `produced` is where
    the numbers came from, when a run directory, a record.json or --commit says so. `command` is what the caller said
    produced the numbers; rb did not run it."""
    model_config = ConfigDict(extra="allow")
    at: str
    actor: str = Field(pattern=ACTOR_PATTERN)
    actor_via: str = ""
    via: Via = "cli"
    rb_version: str
    command: Optional[str] = None
    attached: Optional[dict] = None       # {commit, branch, dirty, diff_sha256, untracked}
    produced: dict = Field(default_factory=lambda: {"commit": None, "from": "unknown"})
    environment: dict = Field(default_factory=dict)
    run_record: Optional[dict] = None
    config: Optional[ConfigCheck] = None


class Evidence(Written):
    """Numbers from a run, attached to the experiment they test. Metric names say whose numbers they are:
    `<variant>.<metric>`, `change.<metric>`, or plain for a number about the comparison as a whole."""
    id: str = Field(pattern=ID_PATTERN)
    experiment: str = Field(pattern=ID_PATTERN)
    metrics: dict[str, float] = Field(default_factory=dict)
    per_run: dict[str, Any] = Field(default_factory=dict)        # per-run settings this run used: {seed: 2}
    run: Optional[str] = None
    files: list[FileRef] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
    basis: Basis = "typed"                                      # where the numbers came from: an rb run, a file, the running code (SDK), or typed in
    spec_sha256: str                                             # the settings the evidence was produced under
    settings: dict[str, Any] = Field(default_factory=dict)       # name -> value at attach, so a later change can be named
    spec_frozen: bool = False
    synthetic: bool = False
    fingerprint: str = ""                                        # identical attachments are recognised, not counted twice
    receipt: Receipt
    note: str = Field(default="", max_length=TEXT)

    @field_validator("metrics")
    @classmethod
    def _metrics(cls, v: dict[str, float]) -> dict[str, float]:
        for k, x in v.items():
            if not re.fullmatch(METRIC_PATTERN, k):
                raise ValueError(f"metric names are letters, digits and _.:/- (got {k!r})")
            _finite(x, f"metric {k}")
        return {k: float(x) for k, x in v.items()}


# ---------------------------------------------------------------- questions, hypotheses, assumptions, decisions


class Question(Written):
    id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT)
    status: Literal["open", "answered", "dropped"] = "open"


class Hypothesis(Written):
    """What you expect and why. `accepted` and `rejected` are set by a person's decision, never by a verdict alone."""
    id: str = Field(pattern=ID_PATTERN)
    statement: str = Field(min_length=1, max_length=TEXT)
    question: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    why: str = Field(default="", max_length=TEXT)
    status: Literal["proposed", "active", "accepted", "rejected"] = "proposed"


class Assumption(Written):
    """Something the work takes for granted. A person's accept records it as `assumed`, never as checked."""
    id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT)
    applies_to: list[str] = Field(default_factory=list)
    status: Literal["open", "assumed", "violated"] = "open"


class Decision(BaseModel):
    """A person's call on a claim, hypothesis, assumption, question, experiment or setting (e1/lr: vouching for it), with
    the verdict a claim had at that moment, so a decision is always read against what it was made on."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    subject: str = Field(min_length=1, max_length=160)
    outcome: Literal["accept", "reject", "investigate"]
    why: str = Field(min_length=1, max_length=TEXT)
    by: str = Field(pattern=PERSON_PATTERN)
    at: str
    via: Via = "cli"
    verdict: Optional[VerdictStatus] = None
    evidence: list[str] = Field(default_factory=list)
    value: Any = None                    # for a setting: the value vouched for; the vouch lapses when the value changes
    across: Optional[dict[str, Any]] = None   # for a setting that differs between variants: each variant's value when accepted
    retracted: Optional[Retraction] = None


class Parent(BaseModel):
    """Where a cloned investigation came from. Everything it brought is `inherited` until reproduced here."""
    model_config = ConfigDict(extra="forbid")
    url: str
    version: Optional[str] = None
    cloned_at: str


class Investigation(BaseModel):
    """`.rb/investigation.json`: the root of one line of work's research state."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = LEDGER_VERSION
    id: str = Field(pattern=ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str
    via: Via = "cli"
    parent: Optional[Parent] = None


# ---------------------------------------------------------------- verdicts (computed on every read, never stored)


class Caveat(BaseModel):
    """Something a verdict rests on that is not established. `blocks` caveats keep the claim from being established."""
    model_config = ConfigDict(extra="forbid")
    code: str
    subject: str
    blocks: bool
    text: str


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: str = Field(description="the evidence id")
    value: float = Field(description="the metric's value in that evidence")
    holds: bool = Field(description="whether this value alone meets the criterion")
    margin: float = Field(description="how far inside (positive) or outside (negative) the criterion")
    role: ObservationRole = Field(description="confirmatory decides the verdict; exploratory is shown, never counted; not_counted is excluded, with the reason")
    reason: str = Field(default="", description="why an observation is exploratory or not counted")
    basis: Basis = Field(description="where the number came from: an rb run, a file, the running code through the SDK (logged), or typed in")
    per_run: dict[str, Any] = Field(default_factory=dict, description="the per-run settings of that run, e.g. its seed")


class ClaimVerdict(BaseModel):
    """A claim's standing, computed from the evidence now.

    A claim is **established** only when all of these hold: the verdict is supported (own criterion) or reproduced (cited
    number) on confirmatory evidence, at least `min_n` of it; a person fixed the criterion (they wrote the claim, or froze
    the experiment after the claim existed); every required setting is verified or vouched for by a person, none unknown,
    none in conflict, no source changed; the variants differ only in what the experiment declares it varies; a frozen
    spec still matches its freeze record; a cited number was checked in a file; no confirmatory observation is
    borderline; the evidence is not only synthetic and was not edited outside rb."""
    model_config = ConfigDict(extra="forbid")
    claim: str
    statement: str
    criterion: str
    origin: ClaimOrigin
    status: VerdictStatus = Field(description="untested | supported | refuted | mixed | reproduced | not_reproduced | not_comparable | inherited")
    established: bool = False
    not_established_because: list[str] = Field(default_factory=list, description="the codes of the blocking caveats")
    n: int = Field(default=0, description="confirmatory observations")
    holding: int = Field(default=0, description="confirmatory observations that meet the criterion")
    mean: Optional[float] = None
    sd: Optional[float] = None
    observations: list[Observation] = Field(default_factory=list)
    caveats: list[Caveat] = Field(default_factory=list)
    decision: Optional[str] = None


OBJECT_SCHEMAS: dict[str, type[BaseModel]] = {
    "investigation": Investigation,
    "question": Question,
    "hypothesis": Hypothesis,
    "assumption": Assumption,
    "experiment": Experiment,
    "metric": Metric,
    "claim": Claim,
    "evidence": Evidence,
    "decision": Decision,
    "verdict": ClaimVerdict,
}
