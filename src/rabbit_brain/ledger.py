"""The `.rb/` store: one investigation's research state, as plain files beside the code.

    .rb/investigation.json
    .rb/questions/q1.json     .rb/hypotheses/h1.json    .rb/assumptions/a1.json
    .rb/experiments/<id>.json .rb/claims/c1.json        .rb/evidence/ev1.json
    .rb/decisions/d1.json     .rb/log.jsonl

Files, not a database, so git, a coding agent, CI and a person all read the same state, and a change to it is a diff.
Every write goes through `Ledger`, which applies the rules the objects cannot apply to themselves:

- only `verify_settings` makes a setting `verified`, and it records what it read; that record is re-checked on every read;
- changing a setting's value or source puts it back to `provisional`;
- a frozen experiment's spec changes only with a stated reason, kept as an amendment, and a spec that no longer matches
  its freeze record is reported, whoever changed it;
- freezing, deciding, retracting and amending are a person's calls: an actor that says it is an agent is refused;
- verdicts are computed from the evidence on every read and never written;
- every write happens under a lock on `.rb/` and is appended to `log.jsonl` with who made it.

The actor is `RB_ACTOR` (`human:<name>` or `agent:<name>`), defaulting to `human:<login>`. The tool cannot tell a
person at a shell from an agent at one; an agent is asked to set `RB_ACTOR=agent:<name>`, and the MCP surface will set
it for every call it serves.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import platform
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from pydantic import BaseModel, ValidationError

from . import __version__
from .errors import RBError
from .investigation import (ACTOR_PATTERN, ID_PATTERN, NAME_PATTERN, Amendment, Assumption, Claim, ClaimVerdict, Decision, Evidence,
                            Experiment, FileRef, Freeze, Hypothesis, Investigation, Setting, Observation, Question, Receipt, Retraction,
                            Source)
from .sources import Unresolved, file_ref, git_state, now, resolve, sha256_bytes, still_as_resolved

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: writes are not serialised across processes there
    fcntl = None  # type: ignore[assignment]

DIR = ".rb"

# kind -> (directory, id prefix, model)
KINDS: dict[str, tuple[str, str, type[BaseModel]]] = {
    "question": ("questions", "q", Question),
    "hypothesis": ("hypotheses", "h", Hypothesis),
    "assumption": ("assumptions", "a", Assumption),
    "experiment": ("experiments", "e", Experiment),
    "claim": ("claims", "c", Claim),
    "evidence": ("evidence", "ev", Evidence),
    "decision": ("decisions", "d", Decision),
}

HOLDS = {"own": "supported", "source": "reproduced", "imported": "reproduced"}
FAILS = {"own": "refuted", "source": "diverged", "imported": "diverged"}
SETTLED = {"supported", "reproduced"}
UNSETTLED = {"refuted", "diverged", "contested"}
FLOAT_MAX = sys.float_info.max


def current_actor() -> str:
    raw = os.environ.get("RB_ACTOR")
    if raw is not None:
        if not re.fullmatch(ACTOR_PATTERN, raw):
            raise RBError("E_ACTOR_INVALID", message=f"RB_ACTOR is {raw!r}.")
        return raw
    try:
        login = getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name in some containers
        login = "unknown"
    login = re.sub(r"[^A-Za-z0-9_.@+-]", "_", login)[:80] or "unknown"
    return f"human:{login}"


def human_only(actor: str, what: str) -> None:
    if not actor.startswith("human:"):
        raise RBError("E_HUMAN_ONLY", message=f"{what} is a person's call, and the actor is {actor}.")


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def find_root(start: Optional[Path] = None) -> Optional[Path]:
    """The nearest directory, from `start` upward, holding `.rb/investigation.json`. Like git, so a command works from any
    subdirectory of the project."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / DIR / "investigation.json").is_file():
            return d
    return None


class Ledger:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.dir = self.root / DIR
        self._depth = 0

    # ------------------------------------------------------------ opening

    @classmethod
    def open(cls, start: Optional[Path] = None) -> "Ledger":
        root = find_root(start)
        if root is None:
            raise RBError("E_NO_INVESTIGATION")
        current_actor()    # a malformed RB_ACTOR is reported on the first command, read or write, not on the first write
        led = cls(root)
        led.investigation  # validates the root file now rather than halfway through a command
        return led

    @classmethod
    def init(cls, root: Path, title: str, id: Optional[str] = None, actor: Optional[str] = None) -> "Ledger":
        existing = find_root(root)
        if existing is not None:
            raise RBError("E_INVESTIGATION_EXISTS", message=f"{existing / DIR} already holds an investigation.")
        actor = actor or current_actor()
        slug = id or (re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "investigation")
        inv = _build(Investigation, id=slug, title=title, created_by=actor, created_at=now())
        led = cls(root)
        led.dir.mkdir(parents=True, exist_ok=True)
        with led._locked():
            led._write(led.dir / ".gitignore", ".lock\n*.tmp\n")
            led._write(led.dir / "investigation.json", _dump(inv))
            led._log(actor, "init", "investigation", inv.id, title=title)
        return led

    @property
    def investigation(self) -> Investigation:
        return self._read(self.dir / "investigation.json", Investigation)

    # ------------------------------------------------------------ files and the lock

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """One writer at a time across processes, so two agents attaching evidence at once cannot pick the same id, and a
        freeze cannot be overwritten by a write that loaded the experiment before it. Re-entrant within a Ledger."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            self._depth = 1
            yield
        finally:
            self._depth = 0
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
        except OSError as exc:
            raise RBError("E_WRITE_FAILED", message=f"Could not write {path}: {exc}")

    def _read(self, path: Path, model: type[BaseModel]) -> Any:
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RBError("E_OBJECT_NOT_FOUND", message=f"{path.relative_to(self.root)} does not exist.")
        except (ValidationError, ValueError) as exc:
            problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()] if isinstance(exc, ValidationError) else [str(exc)]
            raise RBError("E_LEDGER_CORRUPT", message=f"{path.relative_to(self.root)} is not valid.", problems=problems)

    def _path(self, kind: str, id: str) -> Path:
        return self.dir / KINDS[kind][0] / f"{id}.json"

    def _save(self, kind: str, obj: BaseModel) -> None:
        self._write(self._path(kind, obj.id), _dump(obj))  # type: ignore[attr-defined]

    def _log(self, actor: str, op: str, kind: str, id: str, **detail: Any) -> None:
        entry = {"at": now(), "actor": actor, "op": op, "kind": kind, "id": id}
        if detail:
            entry["detail"] = detail
        try:
            with (self.dir / "log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise RBError("E_WRITE_FAILED", message=f"Could not append to {self.dir / 'log.jsonl'}: {exc}")

    def log_entries(self, last: Optional[int] = None) -> list[dict]:
        p = self.dir / "log.jsonl"
        if not p.exists():
            return []
        rows = []
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if not isinstance(row, dict):
                raise RBError("E_LEDGER_CORRUPT", message=f".rb/log.jsonl line {n} is not a log entry (a merge conflict?).", problems=[line[:200]])
            rows.append(row)
        return rows[-last:] if last else rows

    def all(self, kind: str) -> list[Any]:
        d = self.dir / KINDS[kind][0]
        if not d.is_dir():
            return []
        items = [self._read(p, KINDS[kind][2]) for p in d.glob("*.json")]
        return sorted(items, key=lambda o: _order(o.id))

    def load(self, kind: str, id: str) -> Any:
        if not re.fullmatch(ID_PATTERN, id) or not self._path(kind, id).exists():
            raise RBError("E_OBJECT_NOT_FOUND", message=f"No {kind} {id!r} in this investigation.")
        return self._read(self._path(kind, id), KINDS[kind][2])

    def kind_of(self, id: str) -> Optional[str]:
        if not re.fullmatch(ID_PATTERN, id):
            return None
        for kind in KINDS:
            if self._path(kind, id).exists():
                return kind
        return None

    def get(self, id: str) -> tuple[str, Any]:
        kind = self.kind_of(id)
        if kind is None:
            raise RBError("E_OBJECT_NOT_FOUND", message=f"Nothing called {id!r} in this investigation.")
        return kind, self.load(kind, id)

    def _new_id(self, kind: str, wanted: Optional[str] = None) -> str:
        """Called under the lock, so the id chosen is still free when the object is written."""
        if wanted:
            if not re.fullmatch(ID_PATTERN, wanted):
                raise RBError("E_OBJECT_INVALID", message=f"Ids are letters, digits, '_', '.' and '-', up to 80 characters (got {wanted!r}).")
            if self.kind_of(wanted) is not None:
                raise RBError("E_OBJECT_INVALID", message=f"{wanted!r} is already taken in this investigation.")
            return wanted
        prefix = KINDS[kind][1]
        d = self.dir / KINDS[kind][0]
        taken = [int(m.group(1)) for p in (d.glob("*.json") if d.is_dir() else []) if (m := re.fullmatch(rf"{prefix}(\d+)", p.stem))]
        n = max(taken, default=0) + 1
        while self.kind_of(f"{prefix}{n}") is not None:
            n += 1
        return f"{prefix}{n}"

    def _require(self, kind: str, id: Optional[str]) -> None:
        if id is not None and (not re.fullmatch(ID_PATTERN, id) or not self._path(kind, id).exists()):
            raise RBError("E_OBJECT_NOT_FOUND", message=f"No {kind} {id!r} in this investigation.")

    # ------------------------------------------------------------ questions, hypotheses, assumptions

    def add_question(self, text: str, actor: Optional[str] = None, id: Optional[str] = None) -> Question:
        actor = actor or current_actor()
        with self._locked():
            q = _build(Question, id=self._new_id("question", id), text=text, created_by=actor, created_at=now())
            self._save("question", q)
            self._log(actor, "add", "question", q.id)
        return q

    def add_hypothesis(self, statement: str, expect: str = "", why: str = "", question: Optional[str] = None,
                       actor: Optional[str] = None, id: Optional[str] = None) -> Hypothesis:
        actor = actor or current_actor()
        with self._locked():
            self._require("question", question)
            h = _build(Hypothesis, id=self._new_id("hypothesis", id), statement=statement, expect=expect, why=why, question=question, created_by=actor, created_at=now())
            self._save("hypothesis", h)
            self._log(actor, "add", "hypothesis", h.id)
        return h

    def add_assumption(self, text: str, applies_to: Iterable[str] = (), actor: Optional[str] = None, id: Optional[str] = None) -> Assumption:
        actor = actor or current_actor()
        applies = list(applies_to)
        with self._locked():
            for e in applies:
                self._require("experiment", e)
            a = _build(Assumption, id=self._new_id("assumption", id), text=text, applies_to=applies, created_by=actor, created_at=now())
            self._save("assumption", a)
            self._log(actor, "add", "assumption", a.id)
        return a

    # ------------------------------------------------------------ experiments and settings

    def add_experiment(self, title: str, id: Optional[str] = None, tests: Iterable[str] = (), baseline: Optional[str] = None,
                       candidate: Optional[str] = None, note: str = "", actor: Optional[str] = None) -> Experiment:
        actor = actor or current_actor()
        tests = list(tests)
        with self._locked():
            for h in tests:
                self._require("hypothesis", h)
            e = _build(Experiment, id=self._new_id("experiment", id), title=title, tests=tests, baseline=baseline, candidate=candidate,
                       note=note, created_by=actor, created_at=now())
            self._save("experiment", e)
            self._log(actor, "add", "experiment", e.id)
            for h in tests:
                hyp = self.load("hypothesis", h)
                if hyp.status == "proposed":
                    hyp.status = "active"   # a hypothesis with an experiment testing it is under investigation
                    self._save("hypothesis", hyp)
                    self._log(actor, "activate", "hypothesis", h, by_experiment=e.id)
        return e

    def claims_of(self, experiment_id: str) -> list[Claim]:
        return [c for c in self.all("claim") if c.experiment == experiment_id]

    def setup_sha(self, exp: Experiment) -> str:
        """What evidence is produced under: what is compared, and every setting's value and whether it is required. A setting's
        status and source are not in it; verifying a setting does not change the setup."""
        return _sha({"experiment": exp.id, "tests": sorted(exp.tests), "baseline": exp.baseline, "candidate": exp.candidate,
                     "settings": sorted(({"name": k.name, "value": k.value, "required": k.required} for k in exp.settings), key=lambda k: k["name"])})

    def spec_sha(self, exp: Experiment, claims: Optional[list[Claim]] = None) -> str:
        """The hash freezing protects: the setup, and every claim's criterion."""
        claims = self.claims_of(exp.id) if claims is None else claims
        return _sha({"setup": self.setup_sha(exp),
                     "claims": sorted(({"id": c.id, "metric": c.metric, "comparator": c.comparator, "target": c.target, "tolerance": c.tolerance} for c in claims), key=lambda c: c["id"])})

    def spec_drift(self, exp: Experiment) -> Optional[str]:
        """Why a frozen experiment's spec no longer matches its freeze record (the last amendment, or the freeze itself), or
        None. A hand edit to a setting, a claim's criterion or a deleted claim shows up here whatever the log says."""
        if exp.frozen is None:
            return None
        expected = exp.amendments[-1].after if exp.amendments else exp.frozen.sha256
        if self.spec_sha(exp) != expected:
            return f"{exp.id}'s spec no longer matches its freeze record ({expected[:12]}): it was changed without an amendment"
        return None

    def _guard_spec(self, exp: Experiment, before: str, after: str, amend: Optional[str], actor: str, change: str) -> bool:
        """Called after a change is applied in memory and before it is saved. True when an amendment was recorded on `exp`,
        which the caller must then save. An --amend with nothing to amend is refused rather than logged as one."""
        changes_frozen_spec = exp.frozen is not None and before != after
        if not changes_frozen_spec:
            if amend:
                why = "the experiment is not frozen" if exp.frozen is None else "this does not change the frozen spec"
                raise RBError("E_OBJECT_INVALID", message=f"--amend has nothing to amend: {why}. Run it without --amend.")
            return False
        if not amend:
            raise RBError("E_FROZEN", message=f"Experiment {exp.id} was frozen at {exp.frozen.at} ({exp.frozen.sha256[:12]}); this changes its spec ({change}).")
        exp.amendments.append(_build(Amendment, at=now(), by=actor, reason=amend, change=change[:400], before=before, after=after))
        return True

    def set_setting(self, experiment_id: str, name: str, value: Any = None, *, unknown: bool = False, source: Optional[Source] = None,
                 required: Optional[bool] = None, note: Optional[str] = None, amend: Optional[str] = None, actor: Optional[str] = None) -> Setting:
        """Propose a setting's value (it is then `provisional`) or record it as `unknown`. Only `verify_settings` makes it
        `verified`. Re-proposing exactly the same value with the same source keeps a verified setting verified."""
        actor = actor or current_actor()
        if amend:
            human_only(actor, "Amending a frozen experiment")
        if unknown and value is not None:
            raise RBError("E_OBJECT_INVALID", message="A setting is either unknown or has a value, not both.")
        if source is not None and source.resolved is not None:
            source = source.model_copy(update={"resolved": None})   # only verify_settings writes a resolution
        with self._locked():
            exp = self.load("experiment", experiment_id)
            before = self.spec_sha(exp)
            old = exp.setting(name)
            if not unknown and value is None:
                if old is None:
                    raise RBError("E_OBJECT_INVALID", message=f"Give {name} a value, or record it as unknown.")
                value, unknown = old.value, old.status == "unknown"   # changing only required/note/source keeps the value
            same_value = old is not None and not unknown and _same_value(old.value, value)
            if source is not None:
                new_source = source
            else:
                new_source = old.source if same_value else None   # the old source stated the old value, not this one
            keep_verified = same_value and old.status == "verified" and (source is None or _same_source(old.source, source))
            if keep_verified:
                status, new_source = "verified", old.source
            else:
                status = "unknown" if unknown else "provisional"
                if new_source is not None and new_source.resolved is not None:
                    new_source = new_source.model_copy(update={"resolved": None})
            new_required = (old.required if old is not None else True) if required is None else required
            setting = _build(Setting, name=name, value=None if unknown else value, status=status, required=new_required, source=new_source,
                          note=(old.note if note is None and old is not None else (note or "")), set_by=actor, set_at=now())
            exp.settings = [setting if k.name == name else k for k in exp.settings] if old is not None else [*exp.settings, setting]
            after = self.spec_sha(exp)
            change = _describe_change(name, old, setting)
            amended = self._guard_spec(exp, before, after, amend, actor, change)
            self._save("experiment", exp)
            self._log(actor, "set_setting", "experiment", exp.id, setting=name, change=change, value=setting.value, status=setting.status, required=setting.required,
                      source=new_source.label() if new_source else None,
                      **({"amendment": {"reason": amend, "before": before, "after": after}} if amended else {}))
        return setting

    def verify_settings(self, experiment_id: str, names: Optional[Iterable[str]] = None, actor: Optional[str] = None) -> list[dict]:
        """Resolve each named setting's source (every setting that has a source when none are named). The tool, not the
        caller, decides the outcome; the caller only asks."""
        actor = actor or current_actor()
        wanted = list(names or [])
        with self._locked():
            exp = self.load("experiment", experiment_id)
            for n in wanted:
                if exp.setting(n) is None:
                    raise RBError("E_OBJECT_NOT_FOUND", message=f"Experiment {exp.id} has no setting {n!r}.")
            targets = [exp.setting(n) for n in wanted] if wanted else [k for k in exp.settings if k.source is not None and k.status in ("provisional", "verified")]
            results = []
            changed = False
            for k in targets:
                assert k is not None
                row: dict[str, Any] = {"setting": k.name, "value": k.value, "before": k.status}
                if k.status == "unknown" or k.value is None:
                    row.update(after=k.status, ok=False, code="E_SOURCE_UNRESOLVED", reason="the setting is unknown; propose a value with its source first")
                elif k.status == "imported":
                    row.update(after=k.status, ok=False, code="E_SOURCE_UNRESOLVED", reason="an imported setting is verified by reproducing it here: set it with a source in this investigation")
                elif k.source is None:
                    row.update(after=k.status, ok=False, code="E_SOURCE_UNVERIFIABLE", reason="the setting has no source")
                else:
                    try:
                        res = resolve(k.source, k.value, self.root)
                        k.source = k.source.model_copy(update={"resolved": res})
                        k.status = "verified"
                        row.update(after="verified", ok=True, read=res.text, line=res.line, commit=res.commit, sha256=res.sha256)
                        changed = True
                    except Unresolved as u:
                        if k.status == "verified":
                            k.status = "provisional"   # a verified setting whose source no longer states it is not verified any more
                            k.source = k.source.model_copy(update={"resolved": None})
                            changed = True
                        row.update(after=k.status, ok=False, code=u.code, reason=u.reason)
                results.append(row)
            if changed:
                self._save("experiment", exp)
            for row in results:
                self._log(actor, "verify_setting", "experiment", exp.id, setting=row["setting"], outcome=row["after"], **({"reason": row["reason"]} if not row["ok"] else {}))
        return results

    def freeze(self, experiment_id: str, actor: Optional[str] = None) -> Freeze:
        actor = actor or current_actor()
        human_only(actor, "Freezing an experiment")
        with self._locked():
            exp = self.load("experiment", experiment_id)
            if exp.frozen is not None:
                raise RBError("E_FROZEN", message=f"Experiment {exp.id} is already frozen ({exp.frozen.at}).",
                              fix="Change it with --amend \"<reason>\" on the command that changes it; the amendment is recorded.")
            exp.frozen = Freeze(sha256=self.spec_sha(exp), at=now(), by=actor)
            self._save("experiment", exp)
            self._log(actor, "freeze", "experiment", exp.id, sha256=exp.frozen.sha256)
        return exp.frozen

    # ------------------------------------------------------------ claims

    def add_claim(self, statement: str, *, metric: str, comparator: str, target: float, tolerance: Optional[float] = None,
                  experiment: Optional[str] = None, hypothesis: Optional[str] = None, source: Optional[Source] = None, note: str = "",
                  amend: Optional[str] = None, actor: Optional[str] = None, id: Optional[str] = None) -> Claim:
        """A claim's criterion is fixed once written. A claim citing a source (a paper's table) is checked now: a file
        source that does not state the target is refused; a url or a note is recorded and the verdict says it was not
        checked."""
        actor = actor or current_actor()
        if amend:
            human_only(actor, "Amending a frozen experiment")
        if source is not None and source.resolved is not None:
            source = source.model_copy(update={"resolved": None})
        with self._locked():
            self._require("hypothesis", hypothesis)
            claim = _build(Claim, id=self._new_id("claim", id), statement=statement, experiment=experiment, hypothesis=hypothesis, metric=metric,
                           comparator=comparator, target=target, tolerance=tolerance, origin="source" if source is not None else "own",
                           source=source, note=note, created_by=actor, created_at=now())
            if source is not None and source.verifiable():
                try:
                    claim.source = source.model_copy(update={"resolved": resolve(source, claim.target, self.root)})
                except Unresolved as u:
                    raise RBError(u.code, message=f"The claim's source does not state its target {claim.target:g}: {u.reason}",
                                  fix="Point --source/--quote at the text that states the number, or correct the target.")
            amendment = None
            if experiment is not None:
                exp = self.load("experiment", experiment)
                before = self.spec_sha(exp)
                after = self.spec_sha(exp, [*self.claims_of(exp.id), claim])
                if self._guard_spec(exp, before, after, amend, actor, f"new claim {claim.id}: {claim.criterion()}"):
                    self._save("experiment", exp)
                    amendment = {"reason": amend, "before": before, "after": after}
            elif amend:
                raise RBError("E_OBJECT_INVALID", message="--amend has nothing to amend: the claim is not on an experiment.")
            self._save("claim", claim)
            self._log(actor, "add", "claim", claim.id, criterion=claim.criterion(), experiment=experiment, **({"amendment": amendment} if amendment else {}))
        return claim

    # ------------------------------------------------------------ evidence

    def attach_evidence(self, experiment_id: str, *, metrics: Optional[dict[str, float]] = None, run: Optional[dict] = None,
                        files: Iterable[str] = (), links: Optional[dict[str, str]] = None, command: Optional[str] = None,
                        note: str = "", actor: Optional[str] = None) -> Evidence:
        """Attach numbers to an experiment. `run` is what `evidence_from_run` read from an rb run; `metrics` are numbers from
        anything else. The receipt records the repository state now, when the evidence is attached, and the actor."""
        actor = actor or current_actor()
        values = dict(metrics or {})
        run_record = None
        synthetic = False
        files = list(files)
        if run is not None:
            overlap = sorted(set(values) & set(run["metrics"]))
            if overlap:
                raise RBError("E_OBJECT_INVALID", message=f"--metric repeats what the run already reports: {', '.join(overlap)}.")
            values.update(run["metrics"])
            run_record = run["record"]
            synthetic = run["synthetic"]
            if run.get("file"):
                files.append(run["file"])
        if not values:
            raise RBError("E_OBJECT_INVALID", message="Evidence needs at least one number: --run <run>, --metric name=value, or --from file.json.")
        refs = []
        for f in files:
            try:
                refs.append(FileRef(**file_ref(self.root, f)))
            except FileNotFoundError:
                raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {f}")
            except OSError as exc:
                raise RBError("E_FILE_NOT_FOUND", message=f"{f} could not be read: {exc}")
        with self._locked():
            exp = self.load("experiment", experiment_id)
            receipt = Receipt(at=now(), actor=actor, rb_version=__version__, command=command, git=git_state(self.root),
                              environment={"python": platform.python_version(), "platform": platform.platform()}, run_record=run_record)
            ev = _build(Evidence, id=self._new_id("evidence"), experiment=exp.id, kind="rb_run" if run is not None else "observation", metrics=values,
                        run=run["path"] if run is not None else None, files=[r.model_dump() for r in refs], links=dict(links or {}),
                        spec_sha256=self.spec_sha(exp), setup_sha256=self.setup_sha(exp), spec_frozen=exp.frozen is not None, synthetic=synthetic,
                        receipt=receipt.model_dump(), note=note)
            self._save("evidence", ev)
            self._log(actor, "attach", "evidence", ev.id, experiment=exp.id, metrics=sorted(values))
        return ev

    def retract_evidence(self, evidence_id: str, reason: str, actor: Optional[str] = None) -> Evidence:
        actor = actor or current_actor()
        human_only(actor, "Retracting evidence")
        with self._locked():
            ev = self.load("evidence", evidence_id)
            if ev.retracted is not None:
                raise RBError("E_OBJECT_INVALID", message=f"{ev.id} was already retracted at {ev.retracted.at}: {ev.retracted.reason}")
            ev.retracted = _build(Retraction, at=now(), by=actor, reason=reason)
            self._save("evidence", ev)
            self._log(actor, "retract", "evidence", ev.id, reason=reason)
        return ev

    # ------------------------------------------------------------ decisions

    def decide(self, subject: str, outcome: str, why: str, actor: Optional[str] = None) -> Decision:
        actor = actor or current_actor()
        human_only(actor, "A decision")
        with self._locked():
            kind, obj = self.get(subject)
            if kind in ("evidence", "decision"):
                raise RBError("E_OBJECT_INVALID", message=f"Decisions are made on claims, hypotheses, assumptions, questions and experiments, not on {kind}. To stop evidence counting, retract it.")
            verdict, evidence = None, []
            if kind == "claim":
                v = self.verdict(obj)
                verdict, evidence = v.status, [o.evidence for o in v.observations]
            d = _build(Decision, id=self._new_id("decision"), subject=subject, outcome=outcome, why=why, by=actor, at=now(), verdict=verdict, evidence=evidence)
            self._save("decision", d)
            status_map = {
                "hypothesis": {"accept": "accepted", "reject": "rejected", "investigate": "active"},
                "assumption": {"accept": "holds", "reject": "violated", "investigate": "open"},
                "question": {"accept": "answered", "reject": "dropped", "investigate": "open"},
            }
            if kind in status_map:
                obj.status = status_map[kind][outcome]
                self._save(kind, obj)
            self._log(actor, "decide", kind, subject, outcome=outcome, decision=d.id, verdict=verdict)
        return d

    def decisions_on(self, subject: str) -> list[Decision]:
        return [d for d in self.all("decision") if d.subject == subject]

    # ------------------------------------------------------------ verdicts

    def setting_conditions(self, exp: Experiment, has_origin: Optional[bool] = None) -> tuple[list[str], list[str], list[str]]:
        """(required settings with no usable value, conditions a verdict rests on, verified settings that no longer check out)."""
        if has_origin is None:
            has_origin = self.investigation.origin is not None
        blocking, conditions, stale = [], [], []
        for k in exp.settings:
            if k.value is None:
                if k.required:
                    blocking.append(k.name)
                else:
                    conditions.append(f"{k.name} unknown (optional)")
            elif k.status == "provisional":
                conditions.append(f"{k.name} = {_show(k.value)} is provisional, not verified")
            elif k.status == "imported":
                if has_origin:
                    conditions.append(f"{k.name} = {_show(k.value)} is imported from the parent investigation")
                elif k.required:
                    blocking.append(f"{k.name} (imported, but this investigation has no parent)")
                else:
                    conditions.append(f"{k.name} is imported, but this investigation has no parent")
            elif k.status == "verified" and k.source is not None:
                why = still_as_resolved(k.source, k.value, self.root)
                if why:
                    stale.append(k.name)
                    conditions.append(f"{k.name}: {why}")
        return blocking, conditions, stale

    def verdict(self, claim: Claim) -> ClaimVerdict:
        base = {"claim": claim.id, "statement": claim.statement, "criterion": claim.criterion(), "origin": claim.origin}
        decisions = self.decisions_on(claim.id)
        latest = f"{decisions[-1].outcome} by {decisions[-1].by} ({decisions[-1].id})" if decisions else None
        source_conditions = []
        source_stale = False
        if claim.source is not None:
            if claim.source.resolved is None:
                source_conditions.append(f"the claimed {claim.target:g} rests on {claim.source.label()}, which rb did not check")
            else:
                why = still_as_resolved(claim.source, claim.target, self.root)
                if why:
                    source_stale = True
                    source_conditions.append(f"the claimed number's source: {why}")
        if claim.experiment is None:
            return ClaimVerdict(**base, status="imported" if claim.origin == "imported" else "not_tested",
                                conditions=["not linked to an experiment, so no evidence can test it", *source_conditions], decision=latest)
        exp = self.load("experiment", claim.experiment)
        blocking, conditions, stale = self.setting_conditions(exp)
        conditions = [*source_conditions, *conditions]
        drift = self.spec_drift(exp)
        if drift:
            conditions.insert(0, drift)
        setup_now, spec_now = self.setup_sha(exp), self.spec_sha(exp)
        evid = [e for e in self.all("evidence") if e.experiment == exp.id]
        live = [e for e in evid if e.retracted is None]
        observations = []
        for e in live:
            if claim.metric not in e.metrics:
                continue
            value = e.metrics[claim.metric]
            holds, margin, border = _judge(claim, value)
            if exp.frozen is not None and not e.spec_frozen:
                spec = "before_freeze"
            elif (e.setup_sha256 or e.spec_sha256) == (setup_now if e.setup_sha256 else spec_now):
                spec = "current"
            else:
                spec = "amended_since" if exp.frozen is not None else "changed_since"
            post_hoc = claim.created_at > e.receipt.at
            observations.append(Observation(evidence=e.id, value=value, holds=holds, margin=margin, borderline=border, spec=spec, post_hoc=post_hoc))
            if e.synthetic:
                conditions.append(f"{e.id} is synthetic (rb's example data or its synthetic adapter), not evidence about any model")
        for o in observations:
            if o.borderline:
                conditions.append(f"{o.evidence} is borderline: a tenth of the criterion either way changes the call")
            if o.spec == "before_freeze":
                conditions.append(f"{o.evidence} was attached before the experiment was frozen")
            elif o.spec == "amended_since":
                conditions.append(f"{o.evidence} was produced under a setup the experiment has been amended from since")
            elif o.spec == "changed_since":
                conditions.append(f"{o.evidence} was produced under a different setup: the experiment's settings or baseline changed since")
            if o.post_hoc:
                conditions.append(f"{claim.id} was written after {o.evidence} was attached, so its criterion may have been chosen after seeing the number")
        if not observations:
            status = "imported" if claim.origin == "imported" else "not_tested"
        elif all(o.holds for o in observations):
            status = HOLDS[claim.origin]
        elif not any(o.holds for o in observations):
            status = FAILS[claim.origin]
        else:
            status = "contested"
        synthetic_only = bool(observations) and all(self.load("evidence", o.evidence).synthetic for o in observations)
        established = status in SETTLED and not blocking and not stale and not source_stale and not synthetic_only and drift is None
        return ClaimVerdict(**base, status=status, established=established, observations=observations, conditions=_unique(conditions), blocking=blocking,
                            without_metric=[e.id for e in live if claim.metric not in e.metrics],
                            retracted=[e.id for e in evid if e.retracted is not None], decision=latest)

    # ------------------------------------------------------------ the whole state

    def status(self) -> dict:
        inv = self.investigation
        has_origin = inv.origin is not None
        questions, hypotheses, assumptions = self.all("question"), self.all("hypothesis"), self.all("assumption")
        experiments, claims, evidence, decisions = self.all("experiment"), self.all("claim"), self.all("evidence"), self.all("decision")
        verdicts = [self.verdict(c) for c in claims]
        exp_rows, open_items = [], []
        stale_any = unknown_any = False
        for q in questions:
            if q.status == "open":
                open_items.append({"kind": "question", "id": q.id, "what": f"{q.id} is open: {q.text}"})
        tested = {h for e in experiments for h in e.tests} | {c.hypothesis for c in claims if c.hypothesis}
        for h in hypotheses:
            if h.status in ("proposed", "active") and h.id not in tested:
                open_items.append({"kind": "hypothesis", "id": h.id, "what": f"no experiment or claim tests {h.id}: {h.statement}"})
        for a in assumptions:
            if a.status == "open":
                open_items.append({"kind": "assumption", "id": a.id, "what": f"{a.id} is not checked: {a.text}"})
        for e in experiments:
            blocking, _, stale = self.setting_conditions(e, has_origin)
            drift = self.spec_drift(e)
            counts = {s: sum(1 for k in e.settings if k.status == s) for s in ("verified", "provisional", "unknown", "imported")}
            ev = [x for x in evidence if x.experiment == e.id]
            exp_rows.append({"id": e.id, "title": e.title, "tests": e.tests, "baseline": e.baseline, "candidate": e.candidate,
                             "frozen": e.frozen.model_dump() if e.frozen else None, "amendments": len(e.amendments), "spec_drift": drift,
                             "settings": counts, "blocking": blocking, "stale": stale,
                             "evidence": len([x for x in ev if x.retracted is None]), "retracted": len([x for x in ev if x.retracted is not None])})
            unknown_any = unknown_any or bool(blocking)
            stale_any = stale_any or bool(stale) or drift is not None
            if drift:
                open_items.append({"kind": "experiment", "id": e.id, "what": drift})
            for n in blocking:
                open_items.append({"kind": "setting", "id": f"{e.id}.{n.split(' ')[0]}", "what": f"{e.id}: {n} is unknown and required"})
            for k in e.settings:
                if k.value is None and not k.required:
                    open_items.append({"kind": "setting", "id": f"{e.id}.{k.name}", "what": f"{e.id}: {k.name} is unknown (optional)"})
                elif k.status == "provisional":
                    how = f"run `rb setting verify {e.id} {k.name}`" if k.source is not None and k.source.verifiable() else "find a file or run that states it"
                    open_items.append({"kind": "setting", "id": f"{e.id}.{k.name}", "what": f"{e.id}: {k.name} = {_show(k.value)} is provisional; {how}"})
            for n in stale:
                open_items.append({"kind": "setting", "id": f"{e.id}.{n}", "what": f"{e.id}: {n} was verified and no longer checks out; run `rb setting verify {e.id} {n}`"})
        for v in verdicts:
            if v.status == "not_tested":
                open_items.append({"kind": "claim", "id": v.claim, "what": f"{v.claim} is not tested: {v.statement}"})
            elif v.status in UNSETTLED and v.decision is None:
                open_items.append({"kind": "claim", "id": v.claim, "what": f"{v.claim} is {v.status} and nobody has decided on it: {v.statement}"})
            elif v.status in SETTLED and not v.established:
                why = f"blocked on {', '.join(v.blocking)}" if v.blocking else "it rests on a changed source, a drifted spec or only synthetic data"
                open_items.append({"kind": "claim", "id": v.claim, "what": f"{v.claim} is {v.status} but not established: {why}"})
        gate = {
            "unestablished": any(not v.established for v in verdicts),
            "unknown": unknown_any,
            "untested": any(v.status == "not_tested" for v in verdicts),
            "refuted": any(v.status in UNSETTLED for v in verdicts),
            "stale": stale_any,
        }
        return {
            "investigation": inv.model_dump(mode="json"),
            "root": str(self.root),
            "counts": {"questions": len(questions), "hypotheses": len(hypotheses), "assumptions": len(assumptions), "experiments": len(experiments),
                       "claims": len(claims), "evidence": len(evidence), "decisions": len(decisions)},
            "questions": [q.model_dump(mode="json") for q in questions],
            "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
            "assumptions": [a.model_dump(mode="json") for a in assumptions],
            "experiments": exp_rows,
            "claims": [v.model_dump(mode="json") for v in verdicts],
            "evidence": [{"id": x.id, "experiment": x.experiment, "kind": x.kind, "metrics": sorted(x.metrics), "synthetic": x.synthetic,
                          "attached": x.receipt.at, "by": x.receipt.actor, "retracted": x.retracted is not None} for x in evidence],
            "decisions": [d.model_dump(mode="json") for d in decisions],
            "open": open_items,
            "gate": gate,
        }


# ---------------------------------------------------------------- reading rb runs as evidence


def metric_key(metric_id: str) -> str:
    """A run's metric id as an evidence metric name (letters, digits and _.:/-)."""
    return re.sub(r"[^A-Za-z0-9_.:/-]+", "_", metric_id).strip("_")[:60] or "metric"


def evidence_from_run(bundle: Any, run_dir: Optional[Path], root: Path, given: Optional[Path] = None) -> dict:
    """What an `rb run` / `rb import` contributes as evidence: its summary numbers, named so a claim can point at them
    (`candidate.<metric>`, `baseline.<metric>`, `change.<metric>`, `with_gt`, `regressions`, `flagged`, ...), and the
    fields of its own receipt. A run with no labelled case reports no error numbers at all: an error that was not
    measured is not a zero."""
    from .runs import compute_findings
    from .models import Findings

    findings = None
    if run_dir is not None and (run_dir / "findings.json").exists():
        try:
            findings = Findings.model_validate_json((run_dir / "findings.json").read_text(encoding="utf-8"))
        except (ValidationError, ValueError):
            findings = None
    if findings is None:
        findings = compute_findings(bundle)
    s, m = findings.summary, metric_key(bundle.metric.id)
    metrics: dict[str, float] = {"cases": s.cases, "with_gt": s.with_gt, "regressions": s.regressions, "improved": s.improved,
                                 "flagged": s.flagged, "unstable": s.unstable, "borderline": s.borderline}
    if s.with_gt > 0:
        metrics.update({f"baseline.{m}": s.mean_error.baseline, f"candidate.{m}": s.mean_error.candidate,
                        f"change.{m}": s.mean_error.candidate - s.mean_error.baseline})
    record: dict[str, Any] = {"run_id": bundle.run_id, "source": bundle.source, "metric": bundle.metric.model_dump(), "verdict": findings.verdict.line}
    if m != bundle.metric.id:
        record["metric_key"] = m
    if s.with_gt == 0:
        record["note"] = "no case had ground truth, so no error numbers were attached"
    adapter_id = (bundle.adapter or {}).get("id")
    rec_path = run_dir / "record.json" if run_dir is not None else None
    if rec_path is not None and rec_path.exists():
        raw = rec_path.read_bytes()
        record["record_sha256"] = sha256_bytes(raw)
        try:
            rec = json.loads(raw)
            record.update({k: rec.get(k) for k in ("command", "checkpoints", "dataset", "model_code", "seeds", "adapter_agreement", "environment") if rec.get(k) is not None})
            adapter_id = adapter_id or (rec.get("adapter") or {}).get("id")
        except ValueError:
            pass
    path, file = None, None
    where = run_dir if run_dir is not None else given
    if where is not None:
        try:
            rel = os.path.relpath(os.path.abspath(where), root)
            path = rel if not rel.startswith("..") else os.path.abspath(where)
        except ValueError:
            path = os.path.abspath(where)
        if run_dir is None:
            file = path        # a results file read in place: hash it into the evidence
    return {"metrics": metrics, "record": record, "synthetic": bundle.source == "example" or adapter_id == "synthetic", "path": path, "file": file}


# ---------------------------------------------------------------- helpers


def _clamp(x: float) -> float:
    return x if math.isfinite(x) else math.copysign(FLOAT_MAX, x)


def _judge(claim: Claim, value: float) -> tuple[bool, float, bool]:
    """(holds, margin, borderline). Borderline: moving the criterion by a tenth of itself would change the call, which is
    the rule `rb` already uses for per-case limits."""
    eps = 1e-12 * max(1.0, abs(claim.target))
    if claim.comparator == "within":
        tol = float(claim.tolerance or 0.0)
        margin = _clamp(tol - abs(value - claim.target))
        return margin >= -eps, margin, tol > 0 and abs(margin) < 0.1 * tol
    margin = _clamp(claim.target - value if claim.comparator == "at_most" else value - claim.target)
    scale = abs(claim.target)
    return margin >= -eps, margin, scale > 0 and abs(margin) < 0.1 * scale


def _build(model: type[BaseModel], **fields: Any) -> Any:
    try:
        return model(**fields)
    except ValidationError as exc:
        raise _invalid(exc)


def _invalid(exc: ValidationError) -> RBError:
    problems = [f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()]
    return RBError("E_OBJECT_INVALID", message=problems[0] if len(problems) == 1 else "The values given are not valid.", problems=problems if len(problems) > 1 else [])


def _order(id: str) -> tuple:
    m = re.fullmatch(r"([a-zA-Z_.-]*?)(\d+)", id)
    return (m.group(1), int(m.group(2)), id) if m else (id, -1, id)


def _same_value(a: Any, b: Any) -> bool:
    """Exactly the same value: same JSON type and the same JSON text. 1 is not 1.0, and 1700000001 is not 1700000002."""
    return _canonical(a) == _canonical(b) and type(a) is type(b)


def _same_source(a: Optional[Source], b: Optional[Source]) -> bool:
    if a is None or b is None:
        return a is b
    return a.model_dump(exclude={"resolved"}) == b.model_dump(exclude={"resolved"})


def _describe_change(name: str, old: Optional[Setting], new: Setting) -> str:
    if old is None:
        return f"setting {name}: new, {_show(new.value)}{'' if new.required else ' (optional)'}"
    parts = []
    if not _same_value(old.value, new.value) or old.status != new.status and "unknown" in (old.status, new.status):
        parts.append(f"{_show(old.value)} -> {_show(new.value)}")
    if old.required != new.required:
        parts.append("required -> optional" if old.required else "optional -> required")
    if not _same_source(old.source, new.source):
        parts.append(f"source {old.source.label() if old.source else 'none'} -> {new.source.label() if new.source else 'none'}")
    if old.note != new.note:
        parts.append("note changed")
    return f"setting {name}: " + ("; ".join(parts) if parts else "unchanged")


def _show(value: Any) -> str:
    return "unknown" if value is None else (value if isinstance(value, str) else json.dumps(value))


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
