"""The Python surface of the record: the same objects and rules as the CLI, for the code that does the work.

    import rabbit_brain as rb

    state = rb.open()                                   # the .rb/ in this directory or above (rb.init("...") starts one)
    exp = state.experiment("wide")                      # or state.add_experiment("wide vs base", hypothesis="...", ...)
    exp.spec.set("optim.lr", source="configs/train.yaml#optim.lr")
    exp.spec.set("seed", per_run=True)                  # each run gives its own
    crit = exp.criterion("wide beats base on score", metric="score", subject="wide", reference="base", better=True)

    with exp.run(seed=2, config="outputs/2/.hydra/config.yaml", inputs=["heldout"]) as run:   # heldout: a recorded resource
        ...                                             # evaluate
        run.log({"base.score": base_score, "wide.score": wide_score})
    print(crit.finding().says["headline"], crit.finding().says["comparison"])

Numbers logged in a run block are recorded as one run when the block ends without an error, with a receipt recording the
command line of the script, the actor, and the repository's state. A block that raises records nothing. Every write is
recorded with `via: sdk`, with who made it and the chat session when the runtime says; nothing is refused for who makes
it. The calls (decide, retract, freeze) take `words`: the user's words from the chat, verbatim, that an agent acts on.
What rb says beside a write, as `data.warnings` on the command line (an input whose copy here changed, numbers typed
with no document, numbers that differ from the run already recorded with the same output files, LaTeX in an agent's new
text), comes as a `rabbit_brain.RBWarning` at the line that wrote. Each method that writes the record takes
`dry_run=True`: rb makes every check the write makes and writes nothing, and the call gives back a `DryRun`, with what
the write would give back (`value`) and what it would write (`would_write`); a refusal is raised and a warning warned
as the write would.

Nothing here reads or writes the record itself: every call asks rb (`_rb.call`), the same rb the command line is, and
gives back what it answers.
"""
from __future__ import annotations

import numbers
import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Union

from . import _rb
from ._rb import DryRun
from .errors import RBError
from .record import OBJECT_SCHEMAS, Analysis, Finding, Note, Project, Question, Resource, Setting
from .record import Criterion as CriterionData
from .record import Experiment as ExperimentData
from .record import Run as RunData

PathLike = Union[str, Path]
__all__ = ["open", "init", "clone", "State", "Experiment", "Spec", "Criterion", "Run", "Found", "DryRun"]


def open(path: Optional[PathLike] = None, *, agent: Optional[str] = None) -> "State":  # noqa: A001 - rb.open reads naturally
    """The record in `path` (default: the current directory) or the nearest directory above it holding `.rb/`.
    `agent` records every call as `agent:<name>`, whatever the environment says. Leave it unset to use rb's detected identity."""
    return _opened(_rb.call("open", {"path": _path(path)}, agent=agent), agent)


def init(title: str, path: Optional[PathLike] = None, *, id: Optional[str] = None, agent: Optional[str] = None,
         new_project: bool = False) -> "State":
    """Start the record in `path` (default: the current directory). `title` names the work's theme (a paper's title,
    a model, a line of work); each thing the work is trying to find out is a question of its own (`add_question`).
    Where another worktree of the repository holds the project's record, rb refuses unless `new_project` is True."""
    args = {"title": title, "path": _path(path), "id": id}
    if new_project:
        args["new_project"] = True
    return _opened(_rb.call("init", args, agent=agent), agent)


def clone(source: PathLike, dest: Optional[PathLike] = None, *, code: Optional[str] = None, agent: Optional[str] = None) -> "State":
    """Start a new line of work from an exported bundle. The structure arrives inherited; nothing counts
    until it is reproduced here. The bundle must verify against its manifest."""
    return _opened(_rb.call("clone", {"source": _path(source), "dest": _path(dest), "code": code}, agent=agent), agent)


def _opened(answered: dict, agent: Optional[str]) -> "State":
    _rb.check_version(answered)
    here = answered.get("here")
    return State(Path(answered["root"]), agent=agent, here=Path(here) if here and here != answered["root"] else None)


def _path(p: Optional[PathLike]) -> Optional[str]:
    return os.fspath(p) if p is not None else None


def _shaped(got: Any, shape: Callable[[Any], Any]) -> Any:
    """What a write gives back, made by `shape`; for a dry run, its value made the same way, in its DryRun."""
    if isinstance(got, DryRun):
        return DryRun(shape(got.value), got.would_write)
    return shape(got)


def _number(name: str, v: Any) -> float:
    """A metric value: any real number (numpy and torch scalars included), never a bool or text."""
    if hasattr(v, "item") and not isinstance(v, numbers.Number):
        v = v.item()
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        raise RBError("E_OBJECT_INVALID", message=f"{name}: a run's results are numbers (got {type(v).__name__}).")
    return float(v)


def _uses(items: Iterable[Any]) -> list[tuple[str, str]]:
    """Resources a run read or made: "imagenet-val", "imagenet-val:eval", or ("imagenet-val", "eval")."""
    out = []
    for x in items or ():
        if isinstance(x, (tuple, list)):
            out.append((str(x[0]), str(x[1]) if len(x) > 1 else ""))
        else:
            rid, _, role = str(x).partition(":")
            out.append((rid, role))
    return out


def _script_command() -> Optional[str]:
    """The command line that is running now, as the receipt's `command`: what produced the numbers."""
    if not sys.argv or not sys.argv[0]:
        return None
    head = sys.argv[0]
    return shlex.join((["python"] if head.endswith(".py") else []) + list(sys.argv))


@dataclass
class Found:
    """One piece of activity that is in the repository and not in the record (`state.reconcile()`)."""
    id: str
    path: str
    at: str                        # first seen
    metrics: dict                  # the numbers that matched declared metrics (by the file's own names)
    experiment: Optional[str]      # the unambiguous target, or None
    key: str = ""                  # what a dismissal is filed under: the file's sha
    options: list = field(default_factory=list)   # candidate experiments when ambiguous
    argv: list = field(default_factory=list)      # the rb run add that records it, when a target exists
    facts: list = field(default_factory=list)     # observed context: git state, the command, when
    run_in: str = ""               # the checkout argv runs in, as typed: its paths and code are that checkout's


class State:
    """One project's record: its questions, experiments, criteria, runs and resources, and the views over them."""

    def __init__(self, root: PathLike, *, agent: Optional[str] = None, here: Optional[PathLike] = None) -> None:
        self.root = Path(root)
        self.agent = agent
        # the checkout this process works in, where it is a linked worktree nested in the record's: its receipts and
        # log lines take their code and worktree from there
        self.here = Path(here) if here is not None else None

    def _call(self, op: str, _words: Any = _rb.UNSET, _dry: bool = False, **args: Any) -> Any:
        return _rb.call(op, args, root=self.root, agent=self.agent, words=_words, dry_run=_dry, here=self.here)

    @property
    def actor(self) -> str:
        return self._call("actor")

    def who(self) -> dict:
        """Who rb records this process as, and how it knows: {"id", "via", "runtime", "ignored"}."""
        return self._call("who")

    @property
    def project(self) -> Project:
        """The project's own record: its title, its notes, its checks, where it was cloned from."""
        return Project.model_validate(self._call("project"))

    def set_project_title(self, title: str, *, why: str, words: Optional[str] = None, dry_run: bool = False) -> Project:
        """Change the project's title, keeping its identity and record. The reason and any user words go in the log."""
        return _shaped(self._call("set_project_title", words, dry_run, title=title, why=why), Project.model_validate)

    # -- adding
    def add_question(self, text: str, *, id: Optional[str] = None, parent: Optional[str] = None, dry_run: bool = False) -> str:
        return self._call("add_question", _dry=dry_run, text=text, id=id, parent=parent)

    def move_question(self, question: str, *, parent: Optional[str] = None, why: str = "", words: Optional[str] = None,
                      dry_run: bool = False) -> Question:
        """Move a question under a parent, or to the top when parent is None, keeping the reason in the log."""
        return _shaped(self._call("move_question", words, dry_run, question=question, parent=parent, why=why), Question.model_validate)

    def reword_question(self, question: str, text: str, *, why: str, words: Optional[str] = None, dry_run: bool = False) -> Question:
        """Give a question new wording, kept as a revision: the log keeps the old wording, who changed it and why (with
        the user's words when given); its id, evidence and calls stay. The same wording again writes nothing."""
        return _shaped(self._call("reword_question", words, dry_run, question=question, text=text, why=why), Question.model_validate)

    def file_criterion(self, criterion: str, question: str, *, why: str, when_met: Optional[str] = None,
                       remove: bool = False, amend: Optional[str] = None, words: Optional[str] = None, dry_run: bool = False) -> CriterionData:
        """Declare a criterion's relevance and optional verbatim interpretation for a question; keep earlier declarations."""
        return _shaped(self._call("file_criterion", words, dry_run, criterion=criterion, question=question, why=why, when_met=when_met,
                                  remove=remove, amend=amend), CriterionData.model_validate)

    def relate_criterion(self, criterion: str, relation: str, target: str, *, why: str, orientation: Optional[str] = None,
                         remove: bool = False, amend: Optional[str] = None, words: Optional[str] = None, dry_run: bool = False) -> CriterionData:
        """Declare how two comparisons align; rb reads both differences to check a reversal."""
        return _shaped(self._call("relate_criterion", words, dry_run, criterion=criterion, relation=relation, target=target,
                                  orientation=orientation, remove=remove, why=why, amend=amend), CriterionData.model_validate)

    def expose(self, resource_id: str, how: str, *, why: str = "", at: Optional[str] = None, dry_run: bool = False) -> Resource:
        """Someone looked at a dataset: inspected, selected, evaluated or trained on it. Findings declared later say so."""
        return _shaped(self._call("expose", _dry=dry_run, resource_id=resource_id, how=how, why=why, at=at), Resource.model_validate)

    def add_metric(self, name: str, *, unit: str = "", direction: str = "none", aliases: Iterable[str] = (), description: str = "",
                   dry_run: bool = False) -> str:
        """direction: "minimize", "maximize" or "none"."""
        return self._call("add_metric", _dry=dry_run, name=name, unit=unit, direction=direction, aliases=list(aliases), description=description)

    def add_experiment(self, title: str, *, id: Optional[str] = None, question: Optional[str] = None, hypothesis: str = "", why: str = "",
                       purpose: str = "", baseline: Optional[str] = None, candidates: Iterable[str] = (), varies: Iterable[str] = (),
                       like: Optional[str] = None, note: str = "", questions: Iterable[str] = (), if_holds: str = "",
                       if_not: str = "", dry_run: bool = False) -> "Experiment":
        """An experiment: what is tested, and why. It is planned until its first run or analysis. `if_holds` and `if_not`
        say what to do either way: the decision it gates, the action taken. A dry run gives back a DryRun whose value
        is the id given, or with none given an id drawn for the preview only."""
        eid = self._call("add_experiment", _dry=dry_run, title=title, id=id, question=question, hypothesis=hypothesis, why=why, purpose=purpose,
                         baseline=baseline, candidates=list(candidates), varies=list(varies), like=like, note=note,
                         questions=list(questions), if_holds=if_holds, if_not=if_not)
        return eid if dry_run else Experiment(self, eid)

    def add_note(self, text: str, *, experiment: Optional[str] = None, dry_run: bool = False) -> Note:
        """Words about an experiment, or with no experiment the project: an assumption, a caveat, context."""
        return _shaped(self._call("add_note", _dry=dry_run, text=text, experiment=experiment), Note.model_validate)

    def add_resource(self, id: str, *, type: str, name: str, version: str = "", subset: str = "", sha256: Optional[str] = None,
                     digest: str = "", source: str = "", url: Optional[str] = None, recipe: Optional[str] = None,
                     locations: Iterable[tuple[str, str]] = (), file: Optional[PathLike] = None, note: str = "",
                     dry_run: bool = False) -> Resource:
        """A dataset, checkpoint, code, environment or artifact: what it is, where its bytes are (provider, ref), and how
        to get it again. `file` hashes a local file and records it as the first location."""
        return _shaped(self._call(
            "add_resource", _dry=dry_run, id=id, type=type, name=name, version=version, subset=subset, sha256=sha256, digest=digest,
            source=source, url=url, recipe=recipe, locations=[list(x) for x in locations], file=str(file) if file is not None else None,
            note=note), Resource.model_validate)

    def locate_resource(self, id: str, provider: str, ref: str, note: str = "", dry_run: bool = False) -> Resource:
        return _shaped(self._call("locate_resource", _dry=dry_run, id=id, provider=provider, ref=ref, note=note), Resource.model_validate)

    def availability(self, resource_id: str) -> dict:
        """Where a resource stands now, computed: present, changed, remote, reconstructible or lost, with its identity."""
        return self._call("availability", resource_id=resource_id)

    # -- reading
    def experiment(self, id: str) -> "Experiment":
        self._call("load", kind="experiment", id=id)
        return Experiment(self, id)

    def criterion(self, id: str) -> "Criterion":
        self._call("load", kind="criterion", id=id)
        return Criterion(self, id)

    def get(self, id: str, kind: Optional[str] = None) -> Any:
        """Any record by id, as its model (Question, Experiment, Criterion, Run, Resource, ...). With `kind`, the record
        of that kind and no other."""
        if kind is not None:
            return OBJECT_SCHEMAS[kind].model_validate(self._call("load", kind=kind, id=id))
        got = self._call("get", id=id)
        return OBJECT_SCHEMAS[got["kind"]].model_validate(got["object"])

    def records(self, kind: str, *, live: bool = False) -> list:
        """Every record of a kind (question, experiment, metric, criterion, run, analysis, resource, decision) as its
        model, oldest first. With `live`, the ones not retracted."""
        got = self._call("records", kind=kind, live=bool(live))       # rb refuses a kind that is not one of these
        return [OBJECT_SCHEMAS[kind].model_validate(r) for r in got]

    def finding(self, criterion_id: str) -> Finding:
        return Finding.model_validate(self._call("finding", criterion_id=criterion_id))

    def relations(self, criterion_id: str) -> list[dict]:
        """Every relation the criterion is a side of, declared from either side, read now as `rb show <criterion>`
        reads it (its `relations`): each with its outcome, both comparisons, every declaration and who made it, and
        what stops it being read. `relate_criterion` declares one."""
        return self._call("relations", criterion=criterion_id)

    def status(self, experiment: Optional[str] = None) -> dict:
        """What `rb status --json` puts in data: the tally, what needs a human, what can be done now, every record."""
        return self._call("status", experiment=experiment)

    def context(self) -> str:
        """The Markdown briefing `rb context` prints."""
        return self._call("context")

    def log(self, last: Optional[int] = 20, about: Optional[str] = None) -> list[dict]:
        return self._call("log", last=last, about=about)

    # -- the observation buffer (never the record, never counted)
    def scan(self) -> dict:
        """Look once at where results land and journal what changed: {"new", "changed", "gone", "git_moved"}."""
        return self._call("scan")

    def reconcile(self) -> tuple[list, list]:
        """Scan, then match the journal against the record: (found and unrecorded, each carrying the exact `rb run add`
        argv; unplaced lines). Computed fresh, like a finding; recording one is an ordinary run add, by whoever runs it."""
        got = self._call("reconcile")
        return [Found(**f) for f in got["found"]], list(got["unplaced"])

    def found(self) -> tuple[list, list]:
        """What `reconcile()` returns, from what rb has already seen: no new look at the repository."""
        got = self._call("found")
        return [Found(**f) for f in got["found"]], list(got["unplaced"])

    def uncited_changes(self) -> list[dict]:
        """The .json, .yaml and .toml files that changed and that nothing in the record cites, from what rb has already
        seen, each with its `path`, `since`, `committed`, `new`, `gone`, `key` and rb's own words for it (`said`)."""
        return list(self._call("found").get("uncited_changes", []))

    def dismiss(self, key: str, why: str = "") -> None:
        """Leave something rb found out, on this machine: `key` is a found item's `key`, or a changed file's from
        `uncited_changes()`. The file stays where it is. A session's word leaves it out of that session's own briefings
        alone: the user's screens count it until the user leaves it out too. A word rb cannot tell the writer of is
        refused, as any write is."""
        self._call("dismiss", key=key, why=why)

    def journal(self, last: Optional[int] = 20) -> list[dict]:
        """What rb observed in the repository: file events, git moves, wrapped commands."""
        return self._call("journal", last=last)

    def export(self, out: Optional[str] = None, zip: bool = False, url: Optional[str] = None, no_repo: bool = False,
               with_record: bool = False, replace: bool = False) -> dict:
        """Write the project's page with a manifest hashing it, readable without rb. With `with_record`, the record too, as
        a portable bundle rb clone reopens: its files and log keep who made each write, agent sessions and the user's
        words. With `url` (where that bundle will be published), the page opens with the exact clone command. A file at
        `out` that rb did not write is left as it is (RBError) unless `replace`. Returns {"path", "file_count", "bytes",
        "url", ..., "with_record"}."""
        return self._call("export", out=_path(out) if out else None, zip=bool(zip), url=url, no_repo=bool(no_repo),
                          with_record=bool(with_record), replace=bool(replace))

    # -- calls
    def decide(self, subject: str, action: str, *, why: str, revisit: str = "", words: str = "", past: bool = False, when: str = "",
               source: Optional[str] = None, quote: Optional[str] = None, dry_run: bool = False) -> str:
        """accept, reject or investigate a criterion, an experiment, a question, or a setting ("int8/lr"); continue, park,
        stop, pivot or ship an experiment or a question. Recorded with who made it; `words` are the words given with it,
        verbatim. In an agent session (or `rb.open(agent=...)`) the call is proposed, not made: it changes nothing until
        the user confirms it in their own terminal (rb confirm <id>), and an RBWarning says so. `past` records a call made
        before it was recorded, `when` it was made, as said, and `source` ("<file>[:<line>]") with `quote` the document
        that states it, which rb checks and says beside it. In an agent session a past call waits for the same
        confirmation, whatever document it cites."""
        return self._call("decide", words, dry_run, subject=subject, action=action, why=why, revisit=revisit, past=bool(past), when=when,
                          source=_path(source), quote=quote)

    def retract(self, subject: str, *, why: str, words: str = "", dry_run: bool = False) -> Optional[DryRun]:
        return self._call("retract", words, dry_run, subject=subject, why=why)


def _run(got: dict) -> Optional[RunData]:
    """A run add's answer as the SDK gives it: the run, or None when the same recording is already here."""
    return None if got["duplicate"] else RunData.model_validate(got["run"])


class Experiment:
    """A handle on one experiment. `.data` is the stored record, read fresh each time."""

    def __init__(self, state: State, id: str) -> None:
        self.state, self.id = state, id
        self.spec = Spec(self)

    def __repr__(self) -> str:
        return f"<rb experiment {self.id}>"

    @property
    def data(self) -> ExperimentData:
        return ExperimentData.model_validate(self.state._call("load", kind="experiment", id=self.id))

    @property
    def state_word(self) -> str:
        """planned, open, or what the latest decision on it made it: accepted, rejected, investigating, parked, stopped,
        pivoted, shipped."""
        return self.state._call("experiment_state", experiment=self.id)

    def add_variant(self, name: str, *, role: str, note: str = "", amend: Optional[str] = None, why: str = "",
                    dry_run: bool = False) -> Optional[DryRun]:
        """role: baseline, candidate, control or ablation. `amend` is the reason for the change, on a frozen experiment;
        `why` is the reason for the variant, kept on the log line."""
        return self.state._call("add_variant", _dry=dry_run, experiment=self.id, name=name, role=role, note=note, amend=amend, why=why)

    def note(self, text: str, dry_run: bool = False) -> Note:
        """Words about this experiment: an assumption, a caveat, context. Changes no finding."""
        return _shaped(self.state._call("add_note", _dry=dry_run, text=text, experiment=self.id), Note.model_validate)

    def gate(self, if_holds: Optional[str] = None, if_not: Optional[str] = None, why: str = "", words: Optional[str] = None,
             dry_run: bool = False) -> Optional[DryRun]:
        """Set or revise the decision this experiment gates: the action taken if it holds, and if not; the sentence it was
        said in goes in `words`."""
        return self.state._call("set_gate", words, dry_run, experiment=self.id, if_holds=if_holds, if_not=if_not, why=why)

    def relate(self, kind: str, target: str, note: str = "", dry_run: bool = False) -> Optional[DryRun]:
        """reproduces, extends, follows_from or compares_with another experiment (an id here, a uid, or a manifest's address)."""
        return self.state._call("relate", _dry=dry_run, experiment=self.id, kind=kind, target=target, note=note)

    def criterion(self, statement: str, *, metric: str, at_most: Optional[float] = None, at_least: Optional[float] = None,
                  equals: Optional[float] = None, tolerance: Optional[float] = None, better: bool = False,
                  noninferior: Optional[float] = None, equivalent: Optional[float] = None, subject: str = "", reference: str = "",
                  estimand: str = "", over: str = "mean", role: str = "primary", min_n: int = 1, noise: Optional[float] = None,
                  source: Optional[str] = None, quote: Optional[str] = None, locator: Optional[str] = None, note: str = "",
                  amend: Optional[str] = None, id: Optional[str] = None, questions: Iterable[str] = (),
                  when_met: Optional[str] = None, why: str = "", words: Optional[str] = None,
                  where: Optional[Mapping[str, Any]] = None, subject_where: Optional[Mapping[str, Any]] = None,
                  reference_where: Optional[Mapping[str, Any]] = None, dry_run: bool = False) -> "Criterion":
        """Exactly one rule, over one comparison. Declare it before the evidence that tests it: evidence that already
        existed is labelled so on its timing. A dry run gives back a DryRun whose value is the id given, or with none
        given an id drawn for the preview only."""
        given = [(c, v) for c, v in (("at_most", at_most), ("at_least", at_least), ("equals", equals)) if v is not None]
        given += [("better", None)] if better else []
        given += [(c, v) for c, v in (("noninferior", noninferior), ("equivalent", equivalent)) if v is not None]
        if len(given) != 1:
            raise RBError("E_OBJECT_INVALID", message="A criterion needs exactly one rule: at_most, at_least, equals with tolerance, "
                          "better, noninferior=margin or equivalent=margin.")
        comparator, value = given[0]
        target = value if comparator in ("at_most", "at_least", "equals") else None
        margin = value if comparator in ("noninferior", "equivalent") else None
        cid = self.state._call("add_criterion", words, dry_run, statement=statement, experiment=self.id, metric=metric, comparator=comparator, target=target,
                               tolerance=tolerance, source=source, quote=quote, locator=locator, over=over, min_n=min_n, noise=noise,
                               note=note, amend=amend, id=id, subject=subject, reference=reference, estimand=estimand, margin=margin,
                               role=role, questions=list(questions), when_met=when_met, why=why,
                               where=dict(where or {}), subject_where=dict(subject_where or {}), reference_where=dict(reference_where or {}))
        return cid if dry_run else Criterion(self.state, cid)

    def analysis(self, comparisons: Iterable[Mapping[str, Any]], *, title: str = "", reads: Iterable[str] = (), inputs: Iterable[Any] = (),
                 files: Iterable[PathLike] = (), method: str = "", unit: str = "", units: Optional[int] = None, draws: Optional[int] = None,
                 level: Optional[float] = None, source: Optional[str] = None, quote: Optional[str] = None, computed: bool = False,
                 supersedes: Iterable[str] = (), why: str = "", produced_at: Optional[str] = None, command: Optional[str] = None,
                 note: str = "", id: Optional[str] = None, dry_run: bool = False) -> Any:
        """Record an analysis: the comparisons it produced (dicts with subject, reference, metric, estimand, estimate, lo,
        hi, n, unit), what it read, and how. A reanalysis is an analysis, never a run."""
        got = self.state._call("add_analysis", _dry=dry_run, experiment=self.id, comparisons=[dict(c) for c in comparisons], title=title,
                               reads=list(reads), inputs=_uses(inputs), files=[str(f) for f in files],
                               command=command if command is not None else _script_command(), method=method, unit=unit, units=units,
                               draws=draws, level=level, source=source, quote=quote, computed=computed, supersedes=list(supersedes),
                               why=why, produced_at=produced_at, note=note, id=id)
        return _shaped(got, Analysis.model_validate)

    def criteria(self) -> list["Criterion"]:
        return [Criterion(self.state, cid) for cid in self.state._call("criteria", experiment=self.id)]

    def record(self, metrics: Mapping[str, float], *, variant: Optional[str] = None, per_run: Optional[Mapping[str, Any]] = None,
               config: Optional[PathLike] = None, command: Optional[str] = None, commit: Optional[str] = None,
               files: Iterable[PathLike] = (), links: Optional[Mapping[str, str]] = None, inputs: Iterable[Any] = (),
               outputs: Iterable[Any] = (), host: Optional[Mapping[str, str]] = None, again: Optional[str] = None,
               note: str = "", repeats: Optional[str] = None, produced_at: Optional[str] = None,
               replaces: Optional[str] = None, why: str = "", command_source: Optional[str] = None,
               dry_run: bool = False) -> Optional[RunData]:
        """Record a run now, as logged by the code that produced it. Returns the run, or None when the same recording is
        already here (pass `again` with a reason to record a second execution, or `repeats` to check a run), with an
        RBWarning when the numbers given differ from what it recorded. `replaces` names an invalid run this one replaces:
        it is retracted with `why`, and the two are linked. `why` is kept on the log line. A `command` given for a run
        made before it was recorded reads as given later, not observed, unless `command_source` ("<file>:<line>") names
        a log line written by then that records that launch."""
        got = self.state._call(
            "add_run", _dry=dry_run, experiment=self.id, metrics={k: _number(k, v) for k, v in dict(metrics).items()}, variant=variant,
            per_run=dict(per_run or {}) or None, config=str(config) if config is not None else None, command=command,
            command_source=command_source, commit=commit,
            files=[str(f) for f in files], links=dict(links or {}), again=again, note=note, inputs=_uses(inputs),
            outputs=_uses(outputs), host={k: str(v) for k, v in dict(host or {}).items()}, repeats=repeats, produced_at=produced_at,
            replaces=replaces, why=why)
        return _shaped(got, _run)

    def record_from(self, path: PathLike, *, keys: Iterable[str] = (), table: Optional[str] = None, where: Optional[Mapping[str, Any]] = None,
                    select: Iterable[str] = (), variant: Optional[str] = None, per_run: Optional[Mapping[str, Any]] = None,
                    config: Optional[PathLike] = None, command: Optional[str] = None, commit: Optional[str] = None, files: Iterable[PathLike] = (),
                    links: Optional[Mapping[str, str]] = None, inputs: Iterable[Any] = (), outputs: Iterable[Any] = (),
                    host: Optional[Mapping[str, str]] = None, again: Optional[str] = None, note: str = "", repeats: Optional[str] = None,
                    produced_at: Optional[str] = None, replaces: Optional[str] = None, why: str = "", command_source: Optional[str] = None,
                    dry_run: bool = False) -> Optional[RunData]:
        """Record a run whose numbers are in a file, as `rb run add --from` reads it: a JSON, YAML or TOML object of metric
        names to numbers (`keys` narrows them), or one row of a SQLite database, read and never written: `table`, and
        `where` the row's columns and values ({"seed": 1}); its columns that name a declared metric are the numbers, and
        `select` narrows them. With a row, a `per_run` value or `produced_at` written "@<column>" is that column's value,
        and "@<table>.<column>" (or "@<table>[<link>].<column>") that column of the row of another table the row links.
        The receipt keeps the file, and for a row its table, its key and each value's column. `command` stays empty unless
        given: a script that reads a file did not make its numbers. Returns the run, or None when it is already here."""
        got = self.state._call(
            "add_run", _dry=dry_run, experiment=self.id, from_file=os.fspath(path), keys=list(keys), table=table,
            where=dict(where or {}), select=list(select), variant=variant, per_run=dict(per_run or {}) or None,
            config=str(config) if config is not None else None, command=command, command_source=command_source, commit=commit,
            files=[str(f) for f in files], links=dict(links or {}), again=again, note=note, inputs=_uses(inputs), outputs=_uses(outputs),
            host={k: str(v) for k, v in dict(host or {}).items()}, repeats=repeats, produced_at=produced_at, replaces=replaces, why=why)
        return _shaped(got, _run)

    def run(self, variant: Optional[str] = None, *, per_run: Optional[Mapping[str, Any]] = None, config: Optional[PathLike] = None,
            command: Optional[str] = None, commit: Optional[str] = None, note: str = "", again: Optional[str] = None,
            inputs: Iterable[Any] = (), outputs: Iterable[Any] = (), host: Optional[Mapping[str, str]] = None, dry_run: bool = False,
            **settings: Any) -> "Run":
        """A block whose logged numbers become one run when it ends without an error. Per-run settings go in as keywords
        (`seed=2`) or `per_run={"data.seed": 2}`. The command defaults to this script's own command line. With `dry_run`
        the block records nothing, and `run.record` is the DryRun."""
        merged = {**dict(per_run or {}), **settings}
        return Run(self, variant=variant, per_run=merged, config=config, command=command if command is not None else _script_command(),
                   commit=commit, note=note, again=again, inputs=list(inputs), outputs=list(outputs), host=dict(host or {}), dry_run=dry_run)

    def compare(self) -> dict:
        return self.state._call("compare", experiment=self.id)

    def status(self) -> dict:
        return self.state._call("status", experiment=self.id)

    def rerunnability(self) -> list[dict]:
        """What a rerun of this experiment needs, and where each piece stands now: code, environment, resources, files."""
        return self.state._call("rerunnability", experiment=self.id)

    # -- calls
    def freeze(self, *, why: str, words: str = "", amend: bool = False, dry_run: bool = False) -> Optional[DryRun]:
        """Lock the spec and the criteria before the evidence. With `amend`, a frozen experiment's record is brought to it
        as it stands now (an amendment, with `why`)."""
        return self.state._call("freeze", words, dry_run, experiment=self.id, why=why, amend=amend)


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
            note: Optional[str] = None, verify: bool = True, amend: Optional[str] = None,
            design_factor: Optional[bool] = None, why: Optional[str] = None, dry_run: bool = False) -> Setting:
        """Record a setting and where its value comes from; checked against the source now when it can be. With a
        `file#key` source (or a YAML line, or a database's `results.db#table[column=value].column`) the value is read from the file when not given. `amend` is the reason for the change, on a frozen experiment;
        `why` is the reason for the setting, kept on the log line."""
        return _shaped(self.exp.state._call(
            "set_setting", _dry=dry_run, experiment=self.exp.id, name=name, value=value, source=source, quote=quote, term=term, commit=commit,
            locator=locator, variant=variant, unknown=bool(unknown), per_run=bool(per_run), required=required, cited=cited,
            cited_source=cited_source, note=note, verify=verify, amend=amend, design_factor=design_factor, why=why), Setting.model_validate)

    def verify(self, *names: str, dry_run: bool = False) -> list[dict]:
        """Re-read the sources: one row per setting with ok, the status before and after, and why."""
        return self.exp.state._call("verify_settings", _dry=dry_run, experiment=self.exp.id, names=list(names))

    def vary(self, *names: str, amend: Optional[str] = None, why: str = "", dry_run: bool = False) -> Optional[DryRun]:
        """Declare settings the variants differ in on purpose; any other difference is a confound. `why` is the reason,
        kept on the log line."""
        return self.exp.state._call("declare_varies", _dry=dry_run, experiment=self.exp.id, names=list(names), amend=amend, why=why)


class Criterion:
    def __init__(self, state: State, id: str) -> None:
        self.state, self.id = state, id

    def __repr__(self) -> str:
        return f"<rb criterion {self.id}>"

    def file(self, question: str, *, why: str, when_met: Optional[str] = None, remove: bool = False,
             amend: Optional[str] = None, words: Optional[str] = None, dry_run: bool = False) -> CriterionData:
        return self.state.file_criterion(self.id, question, why=why, when_met=when_met, remove=remove, amend=amend, words=words, dry_run=dry_run)

    def relate(self, relation: str, target: str, *, why: str, orientation: Optional[str] = None, remove: bool = False,
               amend: Optional[str] = None, words: Optional[str] = None, dry_run: bool = False) -> CriterionData:
        return self.state.relate_criterion(self.id, relation, target, why=why, orientation=orientation, remove=remove, amend=amend,
                                           words=words, dry_run=dry_run)

    @property
    def data(self) -> CriterionData:
        return CriterionData.model_validate(self.state._call("load", kind="criterion", id=self.id))

    def finding(self) -> Finding:
        """Computed now from the runs and analyses: the comparison, and the finding on its axes."""
        return self.state.finding(self.id)

    def relations(self) -> list[dict]:
        """Every relation this criterion is a side of, read now: `state.relations(<criterion id>)`."""
        return self.state.relations(self.id)

    @property
    def meets(self) -> bool:
        """Whether its rule is met on the current reading (one axis of the finding, never the whole of it)."""
        return self.finding().rule_outcome == "meets"


class Run:
    """Collects numbers while the work runs; records them as one run when the block ends cleanly."""

    def __init__(self, exp: Experiment, *, variant: Optional[str], per_run: dict, config: Optional[PathLike], command: Optional[str],
                 commit: Optional[str], note: str, again: Optional[str], inputs: Optional[list] = None, outputs: Optional[list] = None,
                 host: Optional[dict] = None, dry_run: bool = False) -> None:
        self.exp, self.variant, self.per_run, self.config, self.command = exp, variant, per_run, config, command
        self.commit, self.note, self.again = commit, note, again
        self.metrics: dict[str, float] = {}
        self.files: list[str] = []
        self.links: dict[str, str] = {}
        self.inputs: list[Any] = list(inputs or [])
        self.outputs: list[Any] = list(outputs or [])
        self.host: dict[str, str] = dict(host or {})
        self.record: Union[RunData, DryRun, None] = None
        self.duplicate = False
        self.dry_run = dry_run

    def log(self, metrics: Optional[Mapping[str, float]] = None, **named: float) -> None:
        """Record numbers: `run.log(val_loss=5.64)` or `run.log({"int8.val_loss": 5.64})`. The last value logged under a
        name is the one recorded."""
        for k, v in {**dict(metrics or {}), **named}.items():
            self.metrics[k] = _number(k, v)

    def artifact(self, path: PathLike) -> None:
        """An output whose hash goes into the run."""
        self.files.append(str(path))

    def link(self, name: str, url: str) -> None:
        """Where else the run lives, e.g. run.link("wandb", wandb.run.url)."""
        self.links[name] = url

    def input(self, resource: str, role: str = "") -> None:
        """A resource this run read (a dataset, a checkpoint), by its id."""
        self.inputs.append((resource, role))

    def output(self, resource: str, role: str = "") -> None:
        """A resource this run made, by its id."""
        self.outputs.append((resource, role))

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            return False                       # a run that failed records nothing; the error propagates
        if not self.metrics:
            raise RBError("E_OBJECT_INVALID", message=f"The run block on {self.exp.id} ended without logging a number: run.log(name=value).")
        got = self.exp.record(self.metrics, variant=self.variant, per_run=self.per_run, config=self.config, command=self.command,
                              commit=self.commit, files=self.files, links=self.links, inputs=self.inputs, outputs=self.outputs,
                              host=self.host, again=self.again, note=self.note, dry_run=self.dry_run)
        self.record, self.duplicate = got, got is None or (isinstance(got, DryRun) and got.value is None)
        return False
