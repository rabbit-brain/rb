"""The record: one project's experiments, as YAML files in `.rb/` beside the code. `rb schema <kind>` prints each
shape, and `rb schema all` the whole format as one JSON Schema (rb-v1.schema.json), the contract every file is
checked against.

The ontology is small and closed on purpose, the same drawers in every project:

    Project
      Question                         what the work is trying to find out; a question may sit under a parent question
      Experiment                       what is tested: purpose, hypothesis, variants, settings, criteria, relations
        Variant                        an arm: baseline, candidate, control, ablation
        Setting                        a value, where it comes from, and whether rb has checked it
        Criterion                      a rule over one comparison, declared by someone at a time: what would count
        Run                            what ran: its results, per-run settings, inputs, outputs, and a receipt
        Analysis                       a computation over saved outputs, and the comparisons it produced, with intervals
      Resource                         a dataset, checkpoint, code, environment or artifact: identity, where it lives,
                                       how to get it again, who has looked at it; availability is computed, never stored
      Decision                         a call on a finding, an experiment, a question or a setting, with what it was
                                       made on and when to revisit it
    Finding                            a criterion read on axes (result, rule, timing, units, integrity, review,
                                       history), computed on every read, never stored

Three rules shape every record here.

1. **Anyone records; rb checks; every entry says who.** A human and an agent write the same record, and each write
   carries who made it, the chat session when known, the reason as given, and the user's words an agent acted on,
   verbatim. A setting anyone gives is `provisional`; it becomes `verified` only when rb reads its source and finds the
   value stated there. A finding is never stored: it is computed from the runs and analyses on every read, and no
   single word stands for all of it.
2. **Unknown is a value.** A setting nobody has found is `unknown`, a missing interval is not a zero-width one, and a
   production time nobody can vouch for is said to be unknown.
3. **Nothing is deleted.** A mistake is retracted with a reason, stays on record, and stops counting. A reading that a
   later analysis replaces stays on record as history.

Identity, location and availability are separate things. A run is the receipt of what happened, and it survives the
machine it happened on; a resource keeps what it was and how to get it again after its bytes are gone.

Every file opens with `rb: 1` (the format) and `kind:`, so a file travels and validates on its own. `stored()` is the
form a record is written in: its fields in order, with what carries nothing (null, empty text, empty lists) left out.
"""
from __future__ import annotations

import copy
import math
import re
import typing
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

RECORD_DIR = ".rb"                                      # the record's directory, beside the code
FORMAT_KEY = "rb"                                       # every record file opens with `rb: 1`
RB_FORMAT = 1
ID_PATTERN = r"^[a-zA-Z0-9_.-]{1,80}$"                  # record ids; no '/', so e1/lr addresses a setting unambiguously
UID_PATTERN = r"^[a-z]{2,4}_[0-9A-HJKMNP-TV-Z]{26}$"    # global identity: a kind prefix and a ULID, e.g. exp_01J9...
ACTOR_PATTERN = r"^(human|agent):[A-Za-z0-9_.@+-]{1,80}$"
SETTING_PATTERN = r"^[A-Za-z0-9_.:-]{1,80}$"             # lr, optim.lr; a variant's own setting is <variant>.<name>
VARIANT_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"               # no '.', so int8.lr splits into variant and setting
METRIC_PATTERN = r"^[A-Za-z0-9_.:/-]{1,80}$"             # val/loss is allowed: W&B names metrics that way
TEXT = 1000

Scalar = Union[StrictBool, StrictInt, StrictFloat, StrictStr]
SettingValue = Optional[Union[Scalar, list[Scalar]]]

Via = Literal["cli", "sdk", "mcp", "api"]
SettingStatus = Literal["unknown", "provisional", "verified", "inherited"]
Role = Literal["baseline", "candidate", "control", "ablation"]
Comparator = Literal["at_most", "at_least", "equals", "better", "noninferior", "equivalent"]   # a criterion's rule
THRESHOLDS = ("at_most", "at_least", "equals")
INTERVAL_RULES = ("better", "noninferior", "equivalent")
Over = Literal["mean", "each", "bound"]     # a threshold on the estimate, on every unit, or on the interval's bound
CriterionRole = Literal["primary", "secondary", "exploratory", "gate"]
Origin = Literal["own", "cited", "inherited"]
Direction = Literal["minimize", "maximize", "none"]
Basis = Literal["run", "file", "logged", "typed"]    # run: read from a run record, on older records; logged: reported through the SDK by the code that produced it
ProducedBasis = Literal["reported", "review", "journal", "git", "file", "recorded", "unknown"]
RelationKind = Literal["reproduces", "extends", "follows_from", "compares_with"]
ResourceType = Literal["dataset", "checkpoint", "code", "environment", "artifact", "other"]
Provider = Literal["local", "url", "git", "wandb", "mlflow", "s3", "gcs", "hf", "runpod", "other"]
Availability = Literal["present", "changed", "remote", "reconstructible", "lost"]
ExposureHow = Literal["inspected", "selected", "evaluated", "trained"]
Action = Literal["accept", "reject", "investigate", "continue", "park", "stop", "pivot", "ship"]
RULING_ACTIONS = ("accept", "reject", "investigate")          # calls on a finding, an experiment, a question or a setting
DIRECTION_ACTIONS = ("continue", "park", "stop", "pivot", "ship")   # calls on a line of work: an experiment or a question
Result = Literal["untested", "gain", "loss", "unresolved", "equivalent", "point_only", "measured", "higher", "lower"]
RuleOutcome = Literal["meets", "misses", "undetermined", "untested"]
Timing = Literal["before", "after_evidence", "after_exposure", "unknown", "untested"]
ESTIMAND_PATTERN = r"^[A-Za-z0-9_.:-]{0,60}$"
ARM_PATTERN = r"^[A-Za-z0-9_.:+-]{0,60}$"                  # an arm an analysis compares: a variant, or any named method


def _finite(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{name} must be a finite number")
    return float(v)


def _declared(x: float) -> str:
    """A rule's own number as it was declared, 6 significant digits at least and 15 at most: 1234.5678, never
    1234.57, and 5.4 * 0.01 is 0.054, never 0.054000000000000006."""
    return next((s for k in range(6, 16) if float(s := f"{x:.{k}g}") == x), f"{x:.15g}")


class Retraction(BaseModel):
    """A mistake is retracted, never deleted: it stays on record with who and why, and stops counting. `words` are the
    user's words an agent retracted it on, verbatim, when it gave them.

    `scope` makes a retraction partial, for runs only: it names the measurements the retraction supersedes (a metric's
    name, or its last segment), and the run keeps counting for everything else. A timing bug that left the numerical
    outputs untouched supersedes the timings, not the run."""
    model_config = ConfigDict(extra="forbid")
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    why: str = Field(min_length=1, max_length=TEXT)
    scope: list[str] = Field(default_factory=list, max_length=16)
    words: str = Field(default="", max_length=TEXT)

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: list[str]) -> list[str]:
        for s in v:
            if not re.fullmatch(METRIC_PATTERN, s):
                raise ValueError(f"scope entries name measurements: letters, digits and _.:/- (got {s!r})")
        return v


class Written(BaseModel):
    """Who wrote a record, when, and through which surface."""
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
                raise ValueError("a run source needs a path and a JSON pointer, e.g. run:out/seed1/record.json#/seeds/torch")
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
    a cited criterion on the experiment is not comparable rather than not reproduced."""
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
    A required setting that is unknown, or provisional with nobody vouching for it, keeps the experiment's criteria from
    being established. `per_run` settings (a seed) are given by each run rather than fixed in the spec."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=SETTING_PATTERN)
    value: SettingValue = None
    status: SettingStatus = "unknown"
    required: bool = True
    per_run: bool = False
    design_factor: Optional[bool] = None
    source: Optional[Source] = None
    cited: Optional[Cited] = None
    conflict: Optional[Conflict] = None
    failed: Optional[str] = Field(default=None, max_length=400)   # why the last check of this source did not verify it
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
        if self.design_factor is False:     # no designation: an earlier build wrote it for --sample-factor
            self.design_factor = None
        if self.design_factor and not self.per_run:
            raise ValueError("a design factor must be a per-run setting")
        if self.per_run:
            if self.value is not None or self.status != "unknown":
                raise ValueError(f"setting {self.name}: a per-run setting has no single value; each run gives its own")
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
    """Pre-registration: the spec and the criteria locked, by whoever locked them, with the user's words an agent
    locked them on, when it gave them."""
    model_config = ConfigDict(extra="forbid")
    sha256: str
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    why: str = Field(default="", max_length=TEXT)
    after_runs: list[str] = Field(default_factory=list)   # runs already recorded when it was frozen: their evidence predates the lock
    words: str = Field(default="", max_length=TEXT)


class Amendment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: str
    by: str = Field(pattern=ACTOR_PATTERN)
    why: str = Field(min_length=1, max_length=TEXT)
    change: str = Field(max_length=400)
    before: str
    after: str
    words: str = Field(default="", max_length=TEXT)


class Note(BaseModel):
    """Words about an experiment that are not a criterion: an assumption the work takes for granted, a caveat, context.
    Kept verbatim with who wrote them; a note never changes a finding. Each note is a file of its own, by its id, so
    notes written on two branches never meet in one file; a note from before that has no id until rb next writes its
    experiment. A note taken back (`rb retract <note id>`) stays with `retracted` and leaves the screens."""
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = Field(default=None, pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT)
    by: str = Field(pattern=ACTOR_PATTERN)
    at: str
    via: Via = "cli"
    retracted: Optional[Retraction] = None


class Relation(BaseModel):
    """How this experiment stands to another one: it reproduces it, extends it, follows from it, or compares with it.
    `target` is the other experiment's id here, its global uid, or the address of a published manifest."""
    model_config = ConfigDict(extra="forbid")
    kind: RelationKind
    target: str = Field(min_length=1, max_length=600)
    note: str = Field(default="", max_length=TEXT)
    by: str = Field(pattern=ACTOR_PATTERN)
    at: str


FileRole = Literal["report", "manifest", "results", "audit", "reproduction", "protocol", "code", "check", "data", "other"]
GitHeld = Literal["tracked", "ignored", "untracked", "mixed", "unknown"]
QuoteKind = Literal["title", "summary", "result", "limitation", "next", "command"]


class RepoFile(BaseModel):
    """A file of the repository an experiment rests on, indexed where it is and never copied: its path, what it is to
    the experiment, its hash and size when rb indexed it, and how git held it then. A folder of many large files (arrays,
    images) is one entry, with how many files and bytes it held. Whether each is still there and still the same bytes is
    computed when read."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=400)
    role: FileRole
    sha256: Optional[str] = None
    bytes: int = Field(default=0, ge=0)
    files: int = Field(default=1, ge=0)
    git: GitHeld = "unknown"


class Quote(BaseModel):
    """Words from the repository's own files, verbatim, with where they are: a report's title and opening lines, a
    section its own heading calls a result, a limitation or a next step, a command in a reproduction guide. rb chooses
    the span by the file's headings and blocks; every word is the file's. `cut` says the span goes on past what rb keeps."""
    model_config = ConfigDict(extra="forbid")
    kind: QuoteKind
    text: str = Field(min_length=1, max_length=TEXT)
    path: str = Field(min_length=1, max_length=400)
    lines: str = Field(pattern=r"^\d+(-\d+)?$")
    heading: str = Field(default="", max_length=200)
    cut: bool = False


class Check(BaseModel):
    """A script of the repository that checks the work (an audit, a test) and how to run it, as the repository's own
    files say (`said_at`: file:line). Its outcome is read, never stored: from a run rb watched (rb journal run), or from
    a file an audit wrote that says whether it passed."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=400)
    command: str = Field(default="", max_length=TEXT)
    said_at: str = Field(default="", max_length=400)


class Indexed(BaseModel):
    """Who indexed a folder of the repository into the record (rb reconcile --apply), when, and at which commit."""
    model_config = ConfigDict(extra="forbid")
    by: str = Field(pattern=ACTOR_PATTERN)
    at: str
    commit: Optional[str] = None


class Experiment(Written):
    """What is tested: why (purpose, hypothesis), how (variants, shared settings, what it varies on purpose), and by what
    rule (its criteria, recorded beside it). A setting that differs between variants without being declared in `varies`
    is a confound. An experiment with no runs yet is a planned direction; it shows as such. It may answer to several
    questions at once.

    When the repository already holds it, as a results folder with its report, manifest and outputs, `folder` names that
    folder and `files`, `quotes` and `checks` index what is there (rb reconcile --apply): the files where they are, by
    hash; what they say, verbatim; the scripts that check them. The record points at the repository; it never copies it."""
    id: str = Field(pattern=ID_PATTERN)
    uid: str = Field(pattern=UID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    questions: list[str] = Field(default_factory=list)
    purpose: str = Field(default="", max_length=TEXT)
    hypothesis: str = Field(default="", max_length=TEXT)
    why: str = Field(default="", max_length=TEXT)
    if_holds: str = Field(default="", max_length=TEXT)        # the decision it gates: what to do when it holds, in its author's words
    if_not: str = Field(default="", max_length=TEXT)          # and what to do when it does not
    varies: list[str] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list)
    settings: list[Setting] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    frozen: Optional[Freeze] = None
    amendments: list[Amendment] = Field(default_factory=list)
    folder: str = Field(default="", max_length=400)
    files: list[RepoFile] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    indexed: Optional[Indexed] = None

    @field_validator("questions")
    @classmethod
    def _questions(cls, v: list[str]) -> list[str]:
        for q in v:
            if not re.fullmatch(ID_PATTERN, q):
                raise ValueError(f"questions are ids: letters, digits, '_', '.' and '-' (got {q!r})")
        if len(set(v)) != len(v):
            raise ValueError("a question is listed twice")
        return v

    @property
    def question(self) -> Optional[str]:
        """The first question it is filed under, or None."""
        return self.questions[0] if self.questions else None

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
        names = [s.name for s in v if s.retracted is None]   # one taken back stays on record beside the one set again
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
    """A measurement the catalogue knows: its unit, which direction is better, and the other names it goes by. A run may
    report a metric that is not declared; it is shown as undeclared rather than dropped."""
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


# ---------------------------------------------------------------- criteria and runs


class QuestionLink(BaseModel):
    """A criterion's declared relevance to a question. `when_met` is its author's verbatim interpretation,
    never an answer inferred by rb. The question's wording is kept so a reframing cannot silently change it.
    Revisions retract the old declaration and keep it beside the new one."""
    model_config = ConfigDict(extra="forbid")
    question: str = Field(pattern=ID_PATTERN)
    question_text: str = Field(min_length=1, max_length=TEXT)
    when_met: str = Field(default="", max_length=TEXT)
    declared_by: str = Field(pattern=ACTOR_PATTERN)
    declared_at: str
    via: Via = "cli"
    why: str = Field(default="", max_length=TEXT)
    words: str = Field(default="", max_length=TEXT)
    session: Optional[str] = None
    retracted: Optional[Retraction] = None


class CriterionRelation(BaseModel):
    """An attributed comparison between two criteria. Alignment is declared explicitly; a reversal is computed
    from both current differences, never asserted by this link. The scopes retain what the alignment meant."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["reverses"] = "reverses"
    target: str = Field(pattern=ID_PATTERN)
    orientation: Literal["same", "opposite"]
    source_scope: dict[str, Any]
    target_scope: dict[str, Any]
    declared_by: str = Field(pattern=ACTOR_PATTERN)
    declared_at: str
    via: Via = "cli"
    why: str = Field(min_length=1, max_length=TEXT)
    words: str = Field(default="", max_length=TEXT)
    session: Optional[str] = None
    retracted: Optional[Retraction] = None


class Criterion(Written):
    """A rule over one comparison, declared by someone at a time, recorded on its experiment: what would count.

    What it compares. `metric` names what is measured, in the form runs report it: `val_loss` (the only candidate's,
    when there is one), `int8.val_loss` (a variant's level), `candidate.val_loss` (by role), or `change.val_loss` (the
    candidate against the baseline). Or, spelled out: `subject` (the arm it is about), `reference` (the arm it is
    compared against; none makes it a level) and the bare `metric`. `estimand` names what is estimated when that is not
    rb's own reading of the runs (a mean over units, paired when there is a reference): `pooled`, `per_class`. An
    analysis that reports the same subject, reference, metric and estimand is read in place of the runs.

    Its rule (`comparator`): a threshold (`at_most`, `at_least`, `equals` with a tolerance) read on the estimate
    (`over: mean`), on every unit (`each`) or on the interval's bound (`bound`); `better` (the interval clear of 0 on
    the better side); `noninferior` (the interval's worse bound inside `margin`); `equivalent` (the interval inside
    ±`margin`). The last three need an interval and the metric's direction.

    Its role: `primary` (what the work claims), `secondary`, `exploratory`, or `gate` (a priority policy for spending
    effort: missing a gate parks a line of work, it never refutes anything). `min_n` counts independent units.

    `origin`: `own` is a rule you set; `cited` is a number someone else stated (a paper's table), which the runs
    reproduce or not; `inherited` came from another project or manifest and stays so until reproduced here. `words`
    are the user's words it was declared on, verbatim, when an agent gave them."""
    id: str = Field(pattern=ID_PATTERN)
    experiment: str = Field(pattern=ID_PATTERN)
    statement: str = Field(min_length=1, max_length=400)
    metric: str = Field(pattern=METRIC_PATTERN)
    subject: str = Field(default="", pattern=ARM_PATTERN)
    reference: str = Field(default="", pattern=ARM_PATTERN)
    estimand: str = Field(default="", pattern=ESTIMAND_PATTERN)
    where: dict[str, Any] = Field(default_factory=dict)
    subject_where: dict[str, Any] = Field(default_factory=dict)
    reference_where: dict[str, Any] = Field(default_factory=dict)
    comparator: Comparator
    target: Optional[float] = None
    tolerance: Optional[float] = Field(default=None, ge=0)
    margin: Optional[float] = Field(default=None, gt=0)
    over: Over = "mean"
    role: CriterionRole = "primary"
    min_n: int = Field(default=1, ge=1, le=100000)
    noise: Optional[float] = Field(default=None, ge=0)
    origin: Origin = "own"
    source: Optional[Source] = None
    words: str = Field(default="", max_length=TEXT)
    note: str = Field(default="", max_length=TEXT)

    question_links: list[QuestionLink] = Field(default_factory=list)
    relations: list[CriterionRelation] = Field(default_factory=list)

    @field_validator("target", "tolerance", "noise", "margin")
    @classmethod
    def _optional_finite(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None else _finite(v, "value")

    @model_validator(mode="after")
    def _rule_shape(self) -> "Criterion":
        for selector in (self.where, self.subject_where, self.reference_where):
            for name, value in selector.items():
                if not re.fullmatch(SETTING_PATTERN, name):
                    raise ValueError("run selectors name per-run settings")
                if type(value) not in (str, bool, int, float) or (isinstance(value, str) and not value.strip()) or (isinstance(value, float) and not math.isfinite(value)):
                    raise ValueError("run selector values must be nonblank text, booleans or finite numbers")
        if self.reference_where and not self.compares():
            raise ValueError("reference_where needs a comparison with a reference")
        if self.estimand and (self.where or self.subject_where or self.reference_where):
            raise ValueError("run selectors read runs, not an analysis's custom estimand")
        for selector in (self.subject_where, self.reference_where):
            if any(name in self.where and (type(self.where[name]) is not type(value) or self.where[name] != value) for name, value in selector.items()):
                raise ValueError("an arm selector contradicts the shared run selector")
        # one active declaration per question, and per kind and target, is the writes' rule: a merge may leave two
        if any(r.target == self.id for r in self.relations):
            raise ValueError("a criterion cannot relate to itself")
        if self.comparator in THRESHOLDS and self.target is None:
            raise ValueError(f"an '{self.comparator}' criterion needs a target")
        if self.comparator == "equals" and self.tolerance is None:
            raise ValueError("an 'equals' criterion needs a tolerance")
        if self.comparator != "equals" and self.tolerance is not None:
            raise ValueError("a tolerance applies only to an 'equals' criterion")
        if self.comparator in INTERVAL_RULES:
            if self.target is not None:
                raise ValueError(f"a '{self.comparator}' rule reads the interval against 0; it takes no target")
            if not self.compares():
                raise ValueError(f"a '{self.comparator}' rule compares two arms: give a reference (or read change.<metric>)")
            if self.over != "mean":
                raise ValueError(f"a '{self.comparator}' rule already reads the interval; --each and --bound are for thresholds")
        if self.comparator in ("noninferior", "equivalent") and self.margin is None:
            raise ValueError(f"a '{self.comparator}' rule needs its margin")
        if self.comparator not in ("noninferior", "equivalent") and self.margin is not None:
            raise ValueError("a margin applies only to a noninferior or equivalent rule")
        if self.reference and self.metric.startswith("change."):
            raise ValueError("change.<metric> already compares with the baseline; give the bare metric with a reference")
        if (self.subject or self.reference) and "." in self.metric and self.metric.split(".", 1)[0] in ("change", "candidate", "baseline"):
            raise ValueError("with a subject or reference, the metric is the bare name (val_loss), not a role or change form")
        if self.origin == "cited" and self.source is None:
            raise ValueError("a cited criterion needs its source")
        return self

    def compares(self) -> bool:
        """Whether it compares two arms (a reference given, or change.<metric>), rather than reading one level."""
        return bool(self.reference) or self.metric.startswith("change.")

    def reads(self) -> str:
        """What it reads, as someone says it: 'change.val_loss', 'int8.val_loss', 'score: new vs old (pooled)'."""
        if self.subject or self.reference:
            arms = f"{self.subject or 'candidate'} vs {self.reference}" if self.reference else (self.subject or "")
            text = f"{self.metric}: {arms}" if arms else self.metric
        else:
            text = self.metric
        scopes = [f"{label} " + ", ".join(f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in sorted(values.items()))
                  for label, values in (("where", self.where), ("subject where", self.subject_where), ("reference where", self.reference_where)) if values]
        return text + (f" ({self.estimand})" if self.estimand else "") + (f" [{'; '.join(scopes)}]" if scopes else "")

    def rule(self) -> str:
        """The whole rule as someone reads it before freezing: what it compares, the rule, and how many units it
        takes, its numbers as declared."""
        x = self.reads()
        if self.comparator == "better":
            rule = f"{x} better, its interval clear of 0"
        elif self.comparator == "noninferior":
            rule = f"{x} no worse than {_declared(self.margin)}, its interval's worse bound inside {_declared(self.margin)}"
        elif self.comparator == "equivalent":
            rule = f"{x} within ±{_declared(self.margin)}, its interval inside ±{_declared(self.margin)}"
        else:
            sign = "=" if self.comparator == "equals" else ("≤" if self.comparator == "at_most" else "≥")
            value = f"{_declared(self.target)} ± {_declared(self.tolerance)}" if self.comparator == "equals" else _declared(self.target)
            head = {"mean": x, "each": f"each unit's {x}",
                    "bound": f"{x}, {'its interval' if self.comparator == 'equals' else ('upper bound' if self.comparator == 'at_most' else 'lower bound')}"}[self.over]
            rule = f"{head} {sign} {value}"
        extra = ([f"n ≥ {self.min_n}"] if self.min_n > 1 else []) + ([f"noise {_declared(self.noise)}"] if self.noise is not None else [])
        return rule + (f" ({', '.join(extra)})" if extra else "")


class FileRef(BaseModel):
    """A file a run named, hashed when it was recorded. Whether it is still there, and still the same bytes, is
    computed when read."""
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    bytes: int


class ConfigCheck(BaseModel):
    """The run's own resolved config (Hydra, W&B, Lightning) compared with the spec: whether
    the run actually used the settings the spec says, not only whether a file states them."""
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: Optional[str] = None                             # of the config file; None when the numbers' own record carried no hash
    matches: list[str] = Field(default_factory=list)
    mismatches: list[dict] = Field(default_factory=list)    # {name, spec, ran}
    absent: list[str] = Field(default_factory=list)          # spec settings the config does not mention


class RowRead(BaseModel):
    """The row of a SQLite database a run's numbers were read from (rb run add --from <database> --table --where).
    `store` is the kind of file rb read the row from (sqlite); whether that file is where the numbers were first
    written, rb does not determine. `path` is the database as the run's files name it, `sha256` its bytes when read;
    `table`, and the row's key: `where`, the columns it was picked by with the row's own values, and `rowid` when the
    table has one. Then, for each value, the column it was read from: `columns` for a metric as the run records it,
    `per_run` for a per-run setting, and `produced_at` for the time the run was made. A key a later rb adds is kept."""
    model_config = ConfigDict(extra="allow")
    store: Literal["sqlite"]
    path: str = Field(min_length=1, max_length=400)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    table: str = Field(min_length=1, max_length=200)
    where: dict[str, Any] = Field(default_factory=dict)
    rowid: Optional[int] = None
    columns: dict[str, str] = Field(default_factory=dict)
    per_run: dict[str, str] = Field(default_factory=dict)
    produced_at: Optional[str] = None


class Agent(BaseModel):
    """The agent that made a write, as rb knew it then: its name (its runtime's, as its actor names it), the version its
    runtime or its MCP client gave, its provider and its model, each with where rb has it from. `provider_from` is
    `said` (rb context --provider, the context tool), `env` (RB_PROVIDER, for one command) or `inferred` (rb's table
    of runtimes); `model_from` is `said` (rb context --model, the context tool) or `env` (RB_MODEL, for one command).
    rb never guesses a model. A key a later rb adds is kept."""
    model_config = ConfigDict(extra="allow")
    name: str
    version: Optional[str] = None
    provider: Optional[str] = None
    provider_from: Optional[str] = None
    model: Optional[str] = None
    model_from: Optional[str] = None


class Receipt(BaseModel):
    """What rb recorded when the run was. `attached` is the repository at that moment; `produced` is where the numbers
    came from, when --commit or --tree says so, and `produced.existed` the earliest sign of the bytes of the file its
    numbers were read from (--from), the journal's first sighting, git's first commit or the file's last write, the
    first two left out when a run here already recorded those bytes, which says whether the run was made before it was
    recorded. `command` is what the caller said produced the numbers; rb did not run it. `session` is the chat session
    that recorded it, when its runtime said, and `agent` what rb knew of the agent that recorded it. `row` is the row of
    a SQLite database its numbers were read from, on a run read from one."""
    model_config = ConfigDict(extra="allow")
    at: str
    actor: str = Field(pattern=ACTOR_PATTERN)
    actor_via: str = ""
    via: Via = "cli"
    rb_version: str
    command: Optional[str] = None
    session: Optional[str] = None
    agent: Optional[Agent] = None
    attached: Optional[dict] = None       # {commit, branch, dirty, diff_sha256, untracked}
    produced: dict = Field(default_factory=lambda: {"commit": None, "from": "unknown"})
    environment: dict = Field(default_factory=dict)
    run_record: Optional[dict] = None     # a run record the numbers came with, on older records
    config: Optional[ConfigCheck] = None
    row: Optional[RowRead] = None


class ResourceUse(BaseModel):
    """A resource a run read (an input) or made (an output), in what role (`dataset`, `checkpoint`, `results`), and
    what rb found when the run was recorded: where the resource stood, and the bytes of the copy here when it is one
    file. The resource says what it is; the use says what this run touched, so a later change is attributable."""
    model_config = ConfigDict(extra="forbid")
    resource: str = Field(pattern=ID_PATTERN)
    role: str = Field(default="", max_length=40)
    availability: Optional[Availability] = None     # at the moment the run was recorded
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")   # of the copy here then, when it is one file


class Run(Written):
    """What ran on an experiment: its results (numbers named `<variant>.<metric>`, `change.<metric>`, or plain for the
    comparison as a whole), the per-run settings it used, the resources it read and made, where it ran, and the receipt.
    The machine can be gone; the run is what survives it.

    A run is one execution, and adds evidence about its unit (by default itself; a `population.unit` setting names a
    per-run setting whose values are the units, such as the photograph). A run that `repeats` another is verification of
    it, never a new unit. `produced_at` is the earliest evidence of when its output existed, and `produced_basis` says
    what that evidence is: reported by the recorder, the journal's first sighting, the file's first commit, the file
    itself, or only the moment it was recorded; or that nobody knows, as for a row of a database read with no time,
    since a database's own times are not its row's."""
    id: str = Field(pattern=ID_PATTERN)
    uid: str = Field(pattern=UID_PATTERN)
    experiment: str = Field(pattern=ID_PATTERN)
    metrics: dict[str, float] = Field(default_factory=dict)
    per_run: dict[str, Any] = Field(default_factory=dict)        # per-run settings this run used: {seed: 2}
    repeats: Optional[str] = Field(default=None, pattern=ID_PATTERN)   # the run this one repeats, as a check of it
    replaces: Optional[str] = Field(default=None, pattern=ID_PATTERN)  # an invalid run this one replaces: that run is retracted, and stays on record
    produced_at: Optional[str] = None
    produced_basis: ProducedBasis = "recorded"
    review_run: Optional[str] = None                             # the run directory it was read from, on older records
    files: list[FileRef] = Field(default_factory=list)
    inputs: list[ResourceUse] = Field(default_factory=list)
    outputs: list[ResourceUse] = Field(default_factory=list)
    host: dict[str, str] = Field(default_factory=dict)           # where it ran, as the caller says: provider, gpu, pod
    links: dict[str, str] = Field(default_factory=dict)
    basis: Basis = "typed"                                      # where the numbers came from: a file, the running code (SDK), typed in, or a run record (older records)
    spec_sha256: str                                             # the settings the run was made under
    settings: dict[str, Any] = Field(default_factory=dict)       # name -> value when recorded, so a later change can be named
    arm_specs: dict[str, str] = Field(default_factory=dict)      # each arm's spec when recorded: a comparison counts the run while its arms are as they were
    spec_frozen: bool = False
    synthetic: bool = False
    fingerprint: str = ""                                        # identical runs are recognised, not counted twice
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


# ---------------------------------------------------------------- resources


class Acquisition(BaseModel):
    """How to get a resource again when no copy is at hand: where it comes from, and the recipe that rebuilds it."""
    model_config = ConfigDict(extra="forbid")
    source: str = Field(default="", max_length=TEXT)            # "the ImageNet-1k benchmark, official validation split"
    url: Optional[str] = Field(default=None, max_length=600)
    recipe: Optional[str] = Field(default=None, max_length=TEXT)  # the command or steps that rebuild it


class Location(BaseModel):
    """One place a resource's bytes were, as someone recorded it. A location can vanish; the resource stays."""
    model_config = ConfigDict(extra="forbid")
    provider: Provider
    ref: str = Field(min_length=1, max_length=600)              # a path, a uri, a W&B artifact name, a pod id
    note: str = Field(default="", max_length=400)
    by: str = Field(pattern=ACTOR_PATTERN)
    at: str


class Exposure(BaseModel):
    """Someone looked at a dataset's contents in a way that can shape what they declare next: inspected its cases,
    selected on it, evaluated on it outside a run, or trained on it. A criterion declared after its evaluation data was
    exposed reads as declared after the data was seen. `at` is when it happened, null when nobody knows (then no
    criterion reading the data can be shown to come before it); `reported` says it was given, not the moment it was
    recorded."""
    model_config = ConfigDict(extra="forbid")
    how: ExposureHow
    at: Optional[str] = None
    by: str = Field(pattern=ACTOR_PATTERN)
    recorded_at: str
    reported: bool = False
    why: str = Field(default="", max_length=TEXT)


class Resource(Written):
    """A dataset, checkpoint, code, environment or artifact an experiment depends on or produces: what it is (identity),
    where it has been (locations), and how to get it again (acquisition). Availability is not stored: it is computed
    on every read as present, changed (a copy is here, but not the bytes the identity names), remote, reconstructible
    or lost, so deleting a 100 GB benchmark copy never loses what the benchmark was. Identity is as exact as what was
    recorded: a sha256 or a digest names the bytes, a version names a release, and a name alone is said to be only
    that. Exposures record who has looked at it and how; runs that evaluate on it expose it too, computed."""
    id: str = Field(pattern=ID_PATTERN)
    uid: str = Field(pattern=UID_PATTERN)
    type: ResourceType
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="", max_length=200)
    subset: str = Field(default="", max_length=TEXT)            # "training split, images 0..99"
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")   # of the file, when it is one file
    digest: str = Field(default="", max_length=200)             # any other fingerprint: a provider's digest, a dataset hash
    acquisition: Optional[Acquisition] = None
    locations: list[Location] = Field(default_factory=list)
    exposures: list[Exposure] = Field(default_factory=list)
    note: str = Field(default="", max_length=TEXT)


# ---------------------------------------------------------------- analyses: computations over saved outputs


class Comparison(BaseModel):
    """One comparison an analysis produced: `subject` against `reference` (none: a level) on `metric`, as `estimand`
    says it was estimated, with the estimate and, when there is one, the interval (`lo`, `hi` at `level`). `unit` and
    `n` say what the interval resampled and how many of them; `source` is where a reported number is stated, checked by
    rb when it was recorded. Its identity is experiment, subject, reference, metric and estimand: an analysis that
    reports the same identity later replaces this reading, which stays on record as history."""
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(default="", pattern=ARM_PATTERN)
    reference: str = Field(default="", pattern=ARM_PATTERN)
    metric: str = Field(pattern=METRIC_PATTERN)
    estimand: str = Field(default="", pattern=ESTIMAND_PATTERN)
    estimate: float
    lo: Optional[float] = None
    hi: Optional[float] = None
    level: Optional[float] = Field(default=None, gt=0, lt=1)
    unit: str = Field(default="", max_length=80)
    n: Optional[int] = Field(default=None, ge=1)
    source: Optional[Source] = None
    note: str = Field(default="", max_length=TEXT)

    @field_validator("estimate", "lo", "hi")
    @classmethod
    def _finite_numbers(cls, v: Optional[float]) -> Optional[float]:
        return None if v is None else _finite(v, "a comparison's number")

    @model_validator(mode="after")
    def _interval(self) -> "Comparison":
        if (self.lo is None) != (self.hi is None):
            raise ValueError("an interval has both bounds: give lo and hi, or neither")
        if self.lo is not None and self.hi is not None:
            if self.lo > self.hi:
                raise ValueError(f"the interval's lower bound {self.lo:g} is above its upper bound {self.hi:g}")
            if not (self.lo - 1e-12 * max(1.0, abs(self.lo)) <= self.estimate <= self.hi + 1e-12 * max(1.0, abs(self.hi))):
                raise ValueError(f"the estimate {self.estimate:g} lies outside its interval [{self.lo:g}, {self.hi:g}]")
        return self

    def key(self) -> tuple[str, str, str, str]:
        return (self.subject, self.reference, self.metric, self.estimand)


class Analysis(Written):
    """A computation over saved outputs, and the comparisons it produced. What it read (runs, other analyses, resources
    by hash), how (its command and the repository at that moment, on its receipt), its method (the resampling unit, how
    many draws, the level), and its basis: `computed` when rb read the numbers from an output file it hashed, `reported`
    when they were typed from a document, each checked against the text that states it. A reanalysis is an analysis,
    never a run: it adds no unit. `supersedes` names earlier analyses it replaces, with `why`; their readings stay as
    history."""
    id: str = Field(pattern=ID_PATTERN)
    uid: str = Field(pattern=UID_PATTERN)
    experiment: str = Field(pattern=ID_PATTERN)
    title: str = Field(default="", max_length=200)
    reads: list[str] = Field(default_factory=list)              # runs and analyses it read, by id
    inputs: list[ResourceUse] = Field(default_factory=list)
    method: str = Field(default="", max_length=200)             # "paired bootstrap over scenes"
    unit: str = Field(default="", max_length=80)                # what it resampled: "scene"
    units: Optional[int] = Field(default=None, ge=1)            # how many of them
    draws: Optional[int] = Field(default=None, ge=1)
    level: Optional[float] = Field(default=None, gt=0, lt=1)
    basis: Literal["computed", "reported"] = "computed"
    files: list[FileRef] = Field(default_factory=list)          # the output file(s) the numbers were read from
    source: Optional[Source] = None                             # for a reported analysis: the document, with a locator
    comparisons: list[Comparison] = Field(min_length=1)
    supersedes: list[str] = Field(default_factory=list)
    why: str = Field(default="", max_length=TEXT)               # why it supersedes them
    produced_at: Optional[str] = None
    produced_basis: ProducedBasis = "recorded"
    receipt: Optional[Receipt] = None
    note: str = Field(default="", max_length=TEXT)

    @model_validator(mode="after")
    def _shape(self) -> "Analysis":
        keys = [c.key() for c in self.comparisons]
        twice = sorted({k for k in keys if keys.count(k) > 1})
        if twice:
            s, r, m, e = twice[0]
            raise ValueError(f"the analysis reports {m} for {s or 'the run'}{' vs ' + r if r else ''}{' (' + e + ')' if e else ''} twice")
        if self.supersedes and not self.why:
            raise ValueError("superseding an analysis needs the reason")
        return self


# ---------------------------------------------------------------- questions, decisions


class Question(Written):
    """What the work is trying to find out. A sub-question names its `parent`; experiments are filed under one or more
    questions; where a question stands is read from its experiments' findings and the decisions made on it, never
    stored. A child's negative is the child's: it never becomes the parent's refutation."""
    id: str = Field(pattern=ID_PATTERN)
    text: str = Field(min_length=1, max_length=TEXT)
    parent: Optional[str] = Field(default=None, pattern=ID_PATTERN)


class CallSource(BaseModel):
    """The document a past call is given from: its path and line, the words quoted from it, and the earliest commit
    that held those words at that path when the call was recorded (none when no commit did)."""
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=400)
    line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=TEXT)
    commit: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")


class Past(BaseModel):
    """A call made before it was recorded (rb decide --past): when, as given ("unknown" when nobody said), and the
    document that states it, when one was given."""
    model_config = ConfigDict(extra="forbid")
    when: str = Field(default="unknown", min_length=1, max_length=200)
    source: Optional[CallSource] = None


class Decision(BaseModel):
    """A call, recorded with what it was made on and when to revisit it.

    `accept`, `reject` and `investigate` are calls on a finding (a criterion), an experiment, a question, or a
    setting (e1/lr: vouching for its value). `continue`, `park`, `stop`, `pivot` and `ship` are calls on a line of work
    (an experiment or a question). A missed gate and `park` is how a line pauses without anything being refuted. Anyone
    may record any of them: each is attributed to who recorded it, with the chat session when known, the reason as
    given (`why`), and, when an agent recorded it on the user's words, those words verbatim (`words`). One recorded in
    an agent session is proposed, not made, until the user confirms it (rb confirm); that authority is computed on
    every read, never stored. `past` marks a call made before it was recorded.

    `basis` is the reading of every finding the call was made on, so a decision is always read against it: when the
    evidence moves, the decision says it was made on an earlier reading. A question call that keeps `inquiry_basis` also
    keeps here the reading of each missed gate it settles, and settles the gate while that reading holds. `revisit` is
    the condition for looking again."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    subject: str = Field(min_length=1, max_length=160)
    action: Action
    why: str = Field(min_length=1, max_length=TEXT)
    revisit: str = Field(default="", max_length=TEXT)
    by: str = Field(pattern=ACTOR_PATTERN)
    at: str
    via: Via = "cli"
    session: Optional[str] = None
    words: str = Field(default="", max_length=TEXT)
    basis: dict[str, Any] = Field(default_factory=dict)      # criterion id -> the reading it was made on, or a gate it settles
    inquiry_basis: Optional[dict[str, Any]] = None  # scoped question reading, with its declared interpretations
    value: Any = None                    # for a setting: the value vouched for; the vouch lapses when the value changes
    across: Optional[dict[str, Any]] = None   # for a setting that differs between variants: each variant's value when accepted
    past: Optional[Past] = None          # a call made before it was recorded: Retrospective
    retracted: Optional[Retraction] = None


class Parent(BaseModel):
    """Where a cloned project came from. Everything it brought is `inherited` until reproduced here."""
    model_config = ConfigDict(extra="forbid")
    url: str
    version: Optional[str] = None
    cloned_at: str


class Project(BaseModel):
    """`.rb/project.yaml`: the root of one project's record."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=ID_PATTERN)
    uid: str = Field(pattern=UID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    created_by: str = Field(pattern=ACTOR_PATTERN)
    created_at: str
    via: Via = "cli"
    parent: Optional[Parent] = None
    notes: list[Note] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)          # the repository's checks no single experiment names
    indexed: Optional[Indexed] = None


# ---------------------------------------------------------------- findings (computed on every read, never stored)


class Caveat(BaseModel):
    """Something worth knowing about what a finding rests on. `problem` caveats are its integrity problems: a source
    that contradicts a setting, an undeclared difference between the arms, a record edited outside rb that it depends
    on. The rest are notes: a setting nobody checked, a number typed in, a run not counted and why."""
    model_config = ConfigDict(extra="forbid")
    code: str
    subject: str
    problem: bool = False
    text: str


class Reading(BaseModel):
    """One value of a comparison: the estimate, the interval when there is one (a missing interval is not a zero-width
    one), what it was estimated over, and where it came from: rb's own reading of the runs (`source: runs`) or an
    analysis (its id)."""
    model_config = ConfigDict(extra="forbid")
    subject: str = ""
    reference: str = ""
    metric: str
    estimand: str = ""
    estimate: Optional[float] = None
    lo: Optional[float] = None
    hi: Optional[float] = None
    level: Optional[float] = None
    method: str = ""
    unit: str = ""
    n: Optional[int] = None
    draws: Optional[int] = None
    source: str = "runs"
    basis: str = "runs"                  # runs | computed | reported
    produced_at: Optional[str] = None
    produced_basis: str = ""
    at: str = ""                         # when an analysis's reading was recorded
    superseded: str = ""                 # on a history entry: why it stopped being the current reading
    why_no_interval: str = ""
    design: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    """One run's number as a finding reads it: the difference (or the level) in that run, the unit it belongs to, and
    whether it counts, with the reason when it does not."""
    model_config = ConfigDict(extra="forbid")
    run: str = Field(description="the run id")
    unit: str = Field(default="", description="the independent unit the run belongs to: the run itself, or a population.unit value")
    arm: str = Field(default="", description="for arms recorded in separate runs: which arm this value is; empty for a difference or a level")
    value: float = Field(description="subject minus reference in that run, or the level")
    counted: bool = Field(description="whether it is in the reading")
    reason: str = Field(default="", description="why it is not counted")
    basis: Basis = Field(description="where the number came from: a file, the running code through the SDK (logged), typed in, or a run record (older records)")
    per_run: dict[str, Any] = Field(default_factory=dict, description="the per-run settings of that run, e.g. its seed")
    produced_at: Optional[str] = None
    produced_basis: str = ""
    before_declared: bool = Field(default=False, description="its output existed before the criterion was declared")
    repeats: Optional[str] = None


class Declared(BaseModel):
    """Who declared a criterion, when, and on which of the user's words; and who froze its experiment, when."""
    model_config = ConfigDict(extra="forbid")
    by: str
    at: str
    words: str = ""
    frozen_by: Optional[str] = None
    frozen_at: Optional[str] = None


class Review(BaseModel):
    """The latest decision on a criterion, and whether the reading it was made on is still the current one."""
    model_config = ConfigDict(extra="forbid")
    decision: str
    action: Action
    by: str
    at: str
    why: str
    current: bool
    was: str = ""                        # the reading it was made on, when that has changed


class Units(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int = 0                           # independent units in the reading
    unit: str = ""                       # what a unit is: run, seed, photo, scene
    runs: int = 0                        # runs counted in it
    repeats: int = 0                     # runs that only repeat a unit already counted
    population: str = ""                 # the declared population.* settings: "photographs = 2 · cases = 8"


class Finding(BaseModel):
    """A criterion read on axes, computed from the runs and analyses now. There is no single badge.

    - `result`: what the current comparison shows, in the metric's direction: gain, loss, unresolved (the interval
      includes 0), equivalent (inside the margin), point_only (no interval), measured (a level), or untested.
    - `rule_outcome`: whether the declared rule is met on that comparison: meets, misses, undetermined (it needs an
      interval, more units, or the metric's direction), untested. A gate that misses is a policy outcome.
    - `missed_units`: the units an `--each` rule found outside its threshold, as the observations name them.
    - `timing`: whether the criterion was declared before its evidence existed and before its evaluation data was
      exposed, and by whom; unknown when a production time cannot be vouched for.
    - `units`: how many independent units the reading rests on, and what a unit is.
    - `integrity`: every problem with what it depends on, local to it: a stale or contradicting source, a confound, a
      record it reads edited outside rb. Empty is ok.
    - `review`: the latest decision on it, and whether it was made on the current reading.
    - `history`: earlier readings of the same comparison, with why each was replaced.

    `says` holds one phrase per axis, the words every surface prints."""
    model_config = ConfigDict(extra="forbid")
    criterion: str
    experiment: str
    statement: str
    rule: str
    origin: Origin
    role: CriterionRole = "primary"
    declared: Optional[Declared] = None
    comparison: Optional[Reading] = None
    result: Result = "untested"
    rule_outcome: RuleOutcome = "untested"
    missed_units: list[str] = Field(default_factory=list, description="the units an --each rule found outside its threshold, "
                                    "each as its observations name its unit")
    timing: Timing = "untested"
    units: Units = Field(default_factory=Units)
    integrity: list[Caveat] = Field(default_factory=list)
    review: Optional[Review] = None
    history: list[Reading] = Field(default_factory=list)
    prospective: Optional[Reading] = None
    observations: list[Observation] = Field(default_factory=list)
    caveats: list[Caveat] = Field(default_factory=list)
    says: dict[str, str] = Field(default_factory=dict)
    n: int = Field(default=0, description="independent units in the current reading")


def _model_in(annotation: Any) -> Optional[type[BaseModel]]:
    """The record model a field holds, directly, in a list, or as Optional; None for anything else."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _model_in(arg)
        if found is not None:
            return found
    return None


def _prune(model: type[BaseModel], data: dict) -> dict:
    out = {}
    for k, v in data.items():
        field = model.model_fields.get(k)
        required = field is not None and field.is_required()
        if not required and (v is None or (isinstance(v, (str, list, dict)) and not v and field is not None)):
            continue
        sub = _model_in(field.annotation) if field is not None else None
        if sub is not None and isinstance(v, dict):
            v = _prune(sub, v)
        elif sub is not None and isinstance(v, list):
            v = [_prune(sub, x) if isinstance(x, dict) else x for x in v]
        out[k] = v
    return out


PROVENANCE = ("created_by", "created_at", "via", "retracted")


def ordered(model: type[BaseModel], data: dict) -> dict:
    """A stored form's keys in the order its record writes them (what it is first, provenance last), values untouched:
    for writing back a form that came from canonical JSON, whose keys are sorted. The content, and its hash, do not
    change."""
    fields = list(model.model_fields)
    if issubclass(model, Written):
        fields = [f for f in fields if f not in PROVENANCE] + [f for f in PROVENANCE if f in fields]
    rank = {f: n for n, f in enumerate(fields)}
    out = {}
    for k in sorted(data, key=lambda k: (rank.get(k, len(rank)), k)):
        v = data[k]
        field = model.model_fields.get(k)
        sub = _model_in(field.annotation) if field is not None else None
        if sub is not None and isinstance(v, dict):
            v = ordered(sub, v)
        elif sub is not None and isinstance(v, list):
            v = [ordered(sub, x) if isinstance(x, dict) else x for x in v]
        out[k] = v
    return out


def stored(record: BaseModel) -> dict:
    """The form a record is written in and hashed as: what it is first, who wrote it and when last, as JSON values,
    without the fields that carry nothing (null, empty text, an empty list or mapping). Values inside a mapping the
    record holds (a run's settings, its per-run values) are kept as they are, nulls included: there they are data."""
    form = _prune(type(record), record.model_dump(mode="json", exclude_none=True))
    if isinstance(record, Written):
        form = {**{k: v for k, v in form.items() if k not in PROVENANCE}, **{k: form[k] for k in PROVENANCE if k in form}}
    return form


SCHEMA_ID = "https://rabbitbrain.ai/schema/rb-v1.schema.json"
FILE_MODELS: dict[str, type[BaseModel]] = {}      # kind -> the model of a one-record file; filled below


def _file_schema(schema: dict, kind: str, title: str) -> dict:
    out = copy.deepcopy(schema)
    out["title"] = title
    out["properties"] = {FORMAT_KEY: {"const": RB_FORMAT, "description": "the format: Rabbit Brain format 1"}, "kind": {"const": kind},
                         **out.get("properties", {})}
    out["required"] = [FORMAT_KEY, "kind", *out.get("required", [])]
    return out


def format_schema() -> dict:
    """Rabbit Brain format 1 as one JSON Schema (draft 2020-12): the shape of every file a record holds, chosen by its `kind`.
    It is the contract: rb writes nothing it does not satisfy, and anything that satisfies it, rb can read."""
    defs: dict[str, Any] = {}
    files: list[dict] = []
    for kind, model in FILE_MODELS.items():
        sch = model.model_json_schema(ref_template="#/$defs/{model}")
        defs.update(sch.pop("$defs", {}))
        name = f"{kind.capitalize()}File"
        defs[name] = _file_schema(sch, kind, f"{kind} file ({RECORD_DIR}/{FILE_PATHS[kind]})")
        files.append({"$ref": f"#/$defs/{name}"})
    crit = Criterion.model_json_schema(ref_template="#/$defs/{model}")
    defs.update(crit.pop("$defs", {}))
    crit["properties"].pop("experiment", None)
    crit["required"] = [r for r in crit.get("required", []) if r != "experiment"]
    crit["title"] = "Criterion (in its experiment's criteria file)"
    defs["CriterionEntry"] = crit
    defs["CriteriaFile"] = {
        "type": "object", "additionalProperties": False, "title": f"criteria file ({RECORD_DIR}/{FILE_PATHS['criteria']})",
        "description": ("Every criterion of one experiment in one file, as rb wrote them before each criterion had a file of its "
                        "own: still read, and moved to one file each the next time rb writes the experiment."),
        "properties": {FORMAT_KEY: {"const": RB_FORMAT}, "kind": {"const": "criteria"}, "experiment": {"type": "string", "pattern": ID_PATTERN},
                       "criteria": {"type": "array", "items": {"$ref": "#/$defs/CriterionEntry"}}},
        "required": [FORMAT_KEY, "kind", "experiment", "criteria"],
    }
    files.append({"$ref": "#/$defs/CriteriaFile"})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_ID, "title": "Rabbit Brain record, format 1",
            "description": ("One file of a Rabbit Brain record: Rabbit Brain YAML (a strict subset of YAML) or its canonical JSON. "
                            "`rb` is the format and `kind` says which shape the rest of the file has. Findings and availability "
                            "are computed from these files on every read and never stored."),
            "type": "object", "oneOf": files, "$defs": dict(sorted(defs.items()))}


# kind -> where a file of that kind lives under .rb/. What is added to an experiment (a variant, a note, a criterion, a run)
# is a file of its own, so work recorded on two branches never meets in one file and merges without a conflict.
FILE_PATHS = {"project": "project.yaml", "question": "questions/<id>.yaml", "experiment": "experiments/<id>/experiment.yaml",
              "variant": "experiments/<experiment>/variants/<name>.yaml",
              "note": "experiments/<experiment>/notes/<id>.yaml or notes/<id>.yaml",
              "criterion": "experiments/<experiment>/criteria/<id>.yaml", "run": "experiments/<experiment>/runs/<id>.yaml",
              "analysis": "experiments/<experiment>/analyses/<id>.yaml",
              "metric": "metrics/<id>.yaml", "resource": "resources/<id>.yaml", "decision": "decisions/<id>.yaml",
              # the one file an earlier rb kept an experiment's criteria in: still read, and moved on the next write
              "criteria": "experiments/<id>/criteria.yaml"}


OBJECT_SCHEMAS: dict[str, type[BaseModel]] = {
    "project": Project,
    "question": Question,
    "experiment": Experiment,
    "metric": Metric,
    "criterion": Criterion,
    "run": Run,
    "analysis": Analysis,
    "resource": Resource,
    "decision": Decision,
    "finding": Finding,
}

FILE_MODELS.update({"project": Project, "question": Question, "experiment": Experiment, "variant": Variant, "note": Note,
                    "criterion": Criterion, "run": Run, "analysis": Analysis, "metric": Metric, "resource": Resource,
                    "decision": Decision})
