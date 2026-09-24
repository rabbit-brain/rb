"""The Python surface of the research state: the same objects and rules as the CLI, for the code that does the work.

    import rabbit_brain as rb

    state = rb.open()                                   # the .rb/ in this directory or above (rb.init("...") starts one)
    exp = state.experiment("int8")
    exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    claim = exp.claim("INT8 costs at most 0.05 px EPE", metric="change.epe", at_most=0.05, min_n=3)

    with exp.run(seed=2, config="outputs/2/.hydra/config.yaml") as run:
        ...                                             # evaluate
        run.log({"fp32.epe": fp32_epe, "int8.epe": int8_epe})
    print(claim.verdict().status)

Numbers logged in a run block are attached as evidence when the block ends without an error, with a receipt recording the
command line of the script, the actor, and the repository's state. A block that raises attaches nothing. Every write is
recorded with `via: sdk`. The four calls that are a person's (freeze, decide, amend, retract what something rests on)
raise `RBError("E_HUMAN_ONLY")` for an agent, carrying the exact `rb` command for the person in `err.extra["handoff"]`.
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from .errors import RBError
from .investigation import Claim as ClaimData
from .investigation import ClaimVerdict, Evidence, Setting
from .investigation import Experiment as ExperimentData
from .ledger import Ledger
from .ledger_cli import parse_source, parse_value

PathLike = Union[str, Path]
__all__ = ["open", "init", "State", "Experiment", "Spec", "Claim", "Run"]


def open(path: Optional[PathLike] = None, *, agent: Optional[str] = None) -> "State":  # noqa: A001 - rb.open reads naturally
    """The research state in `path` (default: the current directory) or the nearest directory above it holding `.rb/`.
    `agent` records every call as `agent:<name>`, whatever the environment says; nothing here names a person."""
    return State(Ledger.open(Path(path) if path is not None else None, via="sdk", agent=agent))


def init(title: str, path: Optional[PathLike] = None, *, id: Optional[str] = None, agent: Optional[str] = None) -> "State":
    """Start the research state in `path` (default: the current directory)."""
    return State(Ledger.init(Path(path) if path is not None else Path.cwd(), title, id=id, via="sdk", agent=agent))


def _handoff(*argv: Any) -> str:
    return shlex.join(["rb", *(str(a) for a in argv if a is not None)])


def _script_command() -> Optional[str]:
    """The command line that is running now, as the receipt's `command`: what produced the numbers."""
    if not sys.argv or not sys.argv[0]:
        return None
    head = sys.argv[0]
    return shlex.join((["python"] if head.endswith(".py") else []) + list(sys.argv))


class State:
    """One investigation: its experiments, claims and evidence, and the views over them."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    @property
    def root(self) -> Path:
        return self.ledger.root

    @property
    def actor(self) -> str:
        return self.ledger.actor().id

    # -- adding
    def add_question(self, text: str, *, id: Optional[str] = None) -> str:
        return self.ledger.add_question(text, id=id).id

    def add_hypothesis(self, statement: str, *, why: str = "", question: Optional[str] = None, id: Optional[str] = None) -> str:
        return self.ledger.add_hypothesis(statement, why=why, question=question, id=id).id

    def add_assumption(self, text: str, *, experiments: Iterable[str] = (), id: Optional[str] = None) -> str:
        return self.ledger.add_assumption(text, applies_to=experiments, id=id).id

    def add_metric(self, name: str, *, unit: str = "", direction: str = "none", aliases: Iterable[str] = (), description: str = "") -> str:
        """direction: "minimize", "maximize" or "none"."""
        return self.ledger.add_metric(name, unit=unit, direction=direction, aliases=aliases, description=description).id

    def add_experiment(self, title: str, *, id: Optional[str] = None, baseline: Optional[str] = None, candidates: Iterable[str] = (),
                       varies: Iterable[str] = (), hypotheses: Iterable[str] = (), like: Optional[str] = None, note: str = "") -> "Experiment":
        e = self.ledger.add_experiment(title, id=id, hypotheses=hypotheses, baseline=baseline, candidates=candidates, varies=varies, note=note, like=like)
        return Experiment(self, e.id)

    # -- reading
    def experiment(self, id: str) -> "Experiment":
        self.ledger.load("experiment", id)
        return Experiment(self, id)

    def claim(self, id: str) -> "Claim":
        self.ledger.load("claim", id)
        return Claim(self, id)

    def get(self, id: str) -> Any:
        """Any object by id, as its model (Question, Experiment, Claim, Evidence, ...)."""
        return self.ledger.get(id)[1]

    def verdict(self, claim_id: str) -> ClaimVerdict:
        return self.ledger.verdict(self.ledger.load("claim", claim_id))

    def status(self, experiment: Optional[str] = None) -> dict:
        """What `rb status --json` puts in data: the tally, what needs a person, what an agent can do, every object."""
        return self.ledger.status(experiment)

    def context(self) -> str:
        """The Markdown handoff pack `rb context` prints."""
        from .ledger_cli import context_markdown
        return context_markdown(self.ledger, self.ledger.status())

    def log(self, last: Optional[int] = 20, about: Optional[str] = None) -> list[dict]:
        return self.ledger.log_entries(last=last, about=about)

    # -- a person's calls
    def decide(self, subject: str, outcome: str, *, why: str) -> str:
        """accept, reject or investigate: a claim, hypothesis, assumption, question, experiment, or a setting ("int8/lr")."""
        return self.ledger.decide(subject, outcome, why, handoff=_handoff("decide", subject, outcome, "-m", why)).id

    def retract(self, subject: str, *, why: str) -> None:
        self.ledger.retract(subject, why, handoff=_handoff("retract", subject, "-m", why))


class Experiment:
    """A handle on one experiment. `.data` is the stored object, read fresh each time."""

    def __init__(self, state: State, id: str) -> None:
        self.state, self.id = state, id
        self.spec = Spec(self)

    def __repr__(self) -> str:
        return f"<rb experiment {self.id}>"

    @property
    def data(self) -> ExperimentData:
        return self.state.ledger.load("experiment", self.id)

    def add_variant(self, name: str, *, role: str, note: str = "", amend: Optional[str] = None) -> None:
        """role: baseline, candidate, control or ablation. `amend` is a person's reason, on a frozen experiment."""
        self.state.ledger.add_variant(self.id, name, role, note=note, amend=amend)

    def claim(self, statement: str, *, metric: str, at_most: Optional[float] = None, at_least: Optional[float] = None,
              equals: Optional[float] = None, tolerance: Optional[float] = None, over: str = "each", min_n: int = 1,
              noise: Optional[float] = None, hypothesis: Optional[str] = None, source: Optional[str] = None, quote: Optional[str] = None,
              locator: Optional[str] = None, note: str = "", amend: Optional[str] = None, id: Optional[str] = None) -> "Claim":
        """Exactly one criterion. Write it before the run that tests it: evidence attached earlier never counts."""
        given = [(c, v) for c, v in (("at_most", at_most), ("at_least", at_least), ("equals", equals)) if v is not None]
        if len(given) != 1:
            raise RBError("E_OBJECT_INVALID", message="A claim needs exactly one criterion: at_most, at_least, or equals with tolerance.")
        comparator, target = given[0]
        src = parse_source(source, quote, None, locator, None, self.state.root) if source else None
        c = self.state.ledger.add_claim(statement, metric=metric, comparator=comparator, target=target, tolerance=tolerance,
                                        experiment=self.id, hypothesis=hypothesis, source=src, over=over, min_n=min_n, noise=noise,
                                        note=note, amend=amend, id=id)
        return Claim(self.state, c.id)

    def claims(self) -> list["Claim"]:
        return [Claim(self.state, c.id) for c in self.state.ledger.claims_of(self.id) if c.retracted is None]

    def attach(self, metrics: Mapping[str, float], *, variant: Optional[str] = None, per_run: Optional[Mapping[str, Any]] = None,
               config: Optional[PathLike] = None, command: Optional[str] = None, commit: Optional[str] = None,
               files: Iterable[PathLike] = (), links: Optional[Mapping[str, str]] = None, again: Optional[str] = None,
               note: str = "", basis: str = "logged") -> Optional[Evidence]:
        """Attach numbers now. Returns the evidence, or None when identical evidence is already attached (pass `again`
        with a reason to attach a deliberate repeat)."""
        got = self.state.ledger.attach_evidence(
            self.id, metrics=dict(metrics), variant=variant, per_run=dict(per_run or {}) or None,
            config=str(config) if config is not None else None, command=command, commit=commit,
            files=[str(f) for f in files], links=dict(links or {}), basis=basis, again=again, note=note)
        return got["evidence"] if got.get("duplicate_of") is None else None

    def run(self, variant: Optional[str] = None, *, per_run: Optional[Mapping[str, Any]] = None, config: Optional[PathLike] = None,
            command: Optional[str] = None, commit: Optional[str] = None, note: str = "", again: Optional[str] = None, **settings: Any) -> "Run":
        """A block whose logged numbers become one piece of evidence when it ends without an error. Per-run settings go in
        as keywords (`seed=2`) or `per_run={"data.seed": 2}`. The command defaults to this script's own command line."""
        merged = {**dict(per_run or {}), **settings}
        return Run(self, variant=variant, per_run=merged, config=config, command=command if command is not None else _script_command(),
                   commit=commit, note=note, again=again)

    def compare(self) -> dict:
        return self.state.ledger.compare(self.id)

    def status(self) -> dict:
        return self.state.ledger.status(self.id)

    # -- a person's call
    def freeze(self, *, why: str) -> None:
        """Lock the spec and the claims' criteria before the runs that count."""
        self.state.ledger.freeze(self.id, why, handoff=_handoff("freeze", self.id, "-m", why))


class Spec:
    """An experiment's settings: `exp.spec.set(...)`, `.verify()`, `.vary(...)`, and `exp.spec["optim.lr"]`."""

    def __init__(self, exp: Experiment) -> None:
        self.exp = exp

    def __getitem__(self, name: str) -> Setting:
        s = self.exp.data.lookup(name)
        if s is None:
            raise KeyError(name)
        return s

    def __contains__(self, name: str) -> bool:
        return self.exp.data.lookup(name) is not None

    def items(self) -> list[tuple[str, Setting]]:
        return list(self.exp.data.all_settings())

    def set(self, name: str, value: Any = None, *, source: Optional[str] = None, quote: Optional[str] = None, term: Optional[str] = None,
            commit: Optional[str] = None, locator: Optional[str] = None, variant: Optional[str] = None, unknown: bool = False,
            per_run: bool = False, required: Optional[bool] = None, cited: Any = None, cited_source: Optional[str] = None,
            note: Optional[str] = None, verify: bool = True, amend: Optional[str] = None) -> Setting:
        """Record a setting and where its value comes from; checked against the source now when it can be. With a
        `file#key` source (or a YAML line) the value is read from the file when not given. `amend` is a person's reason."""
        from .investigation import Cited
        led = self.exp.state.ledger
        src = parse_source(source, quote, commit, locator, term, led.root) if source else None
        full = f"{variant}.{name}" if variant else name
        if value is not None and isinstance(value, str):
            value = parse_value(value)
        if value is None and not unknown and not per_run and src is not None:
            from .ledger_cli import _read_value
            value = _read_value(led, src, full)
        c = None
        if cited is not None:
            c = Cited(value=parse_value(cited) if isinstance(cited, str) else cited,
                      source=parse_source(cited_source, None, None, None, None, led.root) if cited_source else None)
        setting, _ = led.set_setting(self.exp.id, full, value, unknown=unknown, source=src, required=required, per_run=True if per_run else None,
                                     cited=c, note=note, amend=amend, verify=verify)
        return setting

    def verify(self, *names: str) -> list[dict]:
        """Re-read the sources: one row per setting with ok, the status before and after, and why."""
        return self.exp.state.ledger.verify_settings(self.exp.id, list(names) or None)

    def vary(self, *names: str, amend: Optional[str] = None) -> None:
        """Declare settings the variants differ in on purpose; any other difference is a confound."""
        self.exp.state.ledger.declare_varies(self.exp.id, names, amend=amend)


class Claim:
    def __init__(self, state: State, id: str) -> None:
        self.state, self.id = state, id

    def __repr__(self) -> str:
        return f"<rb claim {self.id}>"

    @property
    def data(self) -> ClaimData:
        return self.state.ledger.load("claim", self.id)

    def verdict(self) -> ClaimVerdict:
        """Computed now from the evidence: status, established, and why not."""
        return self.state.ledger.verdict(self.data)

    @property
    def established(self) -> bool:
        return self.verdict().established


class Run:
    """Collects numbers while the work runs; attaches them as one piece of evidence when the block ends cleanly."""

    def __init__(self, exp: Experiment, *, variant: Optional[str], per_run: dict, config: Optional[PathLike], command: Optional[str],
                 commit: Optional[str], note: str, again: Optional[str]) -> None:
        self.exp, self.variant, self.per_run, self.config, self.command = exp, variant, per_run, config, command
        self.commit, self.note, self.again = commit, note, again
        self.metrics: dict[str, float] = {}
        self.files: list[str] = []
        self.links: dict[str, str] = {}
        self.evidence: Optional[Evidence] = None
        self.duplicate = False

    def log(self, metrics: Optional[Mapping[str, float]] = None, **named: float) -> None:
        """Record numbers: `run.log(epe=5.64)` or `run.log({"int8.epe": 5.64})`. The last value logged under a name is the
        one attached."""
        for k, v in {**dict(metrics or {}), **named}.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise RBError("E_OBJECT_INVALID", message=f"{k}: evidence holds numbers (got {type(v).__name__}).")
            self.metrics[k] = float(v)

    def artifact(self, path: PathLike) -> None:
        """An output whose hash goes into the evidence."""
        self.files.append(str(path))

    def link(self, name: str, url: str) -> None:
        """Where else the run lives, e.g. run.link("wandb", wandb.run.url)."""
        self.links[name] = url

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            return False                       # a run that failed is not evidence; the error propagates
        if not self.metrics:
            raise RBError("E_OBJECT_INVALID", message=f"The run block on {self.exp.id} ended without logging a number: run.log(name=value).")
        got = self.exp.attach(self.metrics, variant=self.variant, per_run=self.per_run, config=self.config, command=self.command,
                              commit=self.commit, files=self.files, links=self.links, again=self.again, note=self.note)
        self.evidence, self.duplicate = got, got is None
        return False
