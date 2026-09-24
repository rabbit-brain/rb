"""The `.rb/` store: one investigation's research state, as plain files beside the code.

    .rb/investigation.json    .rb/log.jsonl (every write: who, how, and the hash of what was written)
    .rb/questions/  .rb/hypotheses/  .rb/assumptions/  .rb/experiments/  .rb/metrics/
    .rb/claims/  .rb/evidence/  .rb/decisions/

Files, not a database: git, a coding agent, CI and a person all read the same state, and a change is a diff. Ids carry
a few random characters (c7k2m) so two worktrees never mint the same one, and log.jsonl merges line by line.

Every write goes through `Ledger`, which applies the rules the objects cannot apply to themselves:

- a setting becomes `verified` only through `verify_settings`, and what was read is re-checked on every read;
- changing a setting's value or source puts it back to `provisional`; a source that now states a different value is a
  conflict that re-verifying never clears;
- a frozen experiment's spec changes only with a person's stated reason, kept as an amendment, and a spec that no longer
  matches its freeze record is reported whoever changed it;
- freezing, deciding, amending, and retracting what something rests on are a person's calls;
- verdicts are computed from the evidence on every read and never written;
- every write happens under a lock on `.rb/` and is logged with its actor and the hash of the object written, so an
  object edited outside rb is caught.
"""
from __future__ import annotations

import base64
import hashlib
import gzip
import json
import math
import os
import platform
import re
import secrets
import shlex
import statistics
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from pydantic import BaseModel, ValidationError

from . import __version__
from . import actor as actor_mod
from .actor import Actor
from .errors import RBError
from .investigation import (ID_PATTERN, SETTING_PATTERN, VARIANT_PATTERN, Amendment, Assumption, Caveat, Cited, Claim, ClaimVerdict,
                            ConfigCheck, Conflict, Decision, Evidence, Experiment, FileRef, Freeze, Hypothesis, Investigation,
                            Metric, Observation, Question, Receipt, Retraction, Setting, Source, Variant)
from .sources import (Unresolved, file_ref, flatten, git_state, load_config, now, recheck, resolve, sha256_bytes, structured_kind,
                      yaml_key_at)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: writes are not serialised across processes there
    fcntl = None  # type: ignore[assignment]

DIR = ".rb"
GITIGNORE = ".lock\n*.tmp\nobjects/\n"      # objects/: rb's local copies of what it wrote, for rb doctor --restore

# kind -> (directory, id prefix, model)
KINDS: dict[str, tuple[str, str, type[BaseModel]]] = {
    "question": ("questions", "q", Question),
    "hypothesis": ("hypotheses", "h", Hypothesis),
    "assumption": ("assumptions", "a", Assumption),
    "experiment": ("experiments", "e", Experiment),
    "metric": ("metrics", "m", Metric),
    "claim": ("claims", "c", Claim),
    "evidence": ("evidence", "ev", Evidence),
    "decision": ("decisions", "d", Decision),
}

_UMASK = os.umask(0o022)
os.umask(_UMASK)
RUN_COUNTS = ("cases", "with_gt", "regressions", "improved", "flagged", "unstable", "borderline")   # what a review run reports besides its metric
HOLDS = {"own": "supported", "cited": "reproduced", "inherited": "reproduced"}
FAILS = {"own": "refuted", "cited": "not_reproduced", "inherited": "not_reproduced"}
SETTLED = {"supported", "reproduced"}
UNSETTLED = {"refuted", "not_reproduced", "mixed"}
ROLES = ("baseline", "candidate", "control", "ablation")


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _run_key(e: Any) -> str:
    """Two pieces of evidence with the same per-run values reporting the same numbers are one run reported twice."""
    return "run:" + _canonical({"per_run": e.per_run, "metrics": sorted(e.metrics)})


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def show(value: Any) -> str:
    return "unknown" if value is None else (value if isinstance(value, str) else json.dumps(value))


def find_root(start: Optional[Path] = None) -> Optional[Path]:
    """The nearest directory, from `start` upward, holding `.rb/investigation.json`."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / DIR / "investigation.json").is_file():
            return d
    return None


def parse_address(text: str) -> tuple[str, Optional[str]]:
    """`e1/lr` addresses setting lr of experiment e1; `e1.lr` is accepted too when e1 is an experiment."""
    head, sep, tail = text.partition("/")
    return (head, tail) if sep else (text, None)


_SESSION: ContextVar[tuple[str, Optional[str]]] = ContextVar("rb_session", default=("cli", None))


@contextmanager
def session(via: str, agent: Optional[str] = None) -> Iterator[None]:
    """Every Ledger opened inside this block records `via` as the surface, and, when `agent` is given, records every
    call as that agent whatever RB_ACTOR says. The MCP server serves each connection inside one."""
    token = _SESSION.set((via, agent))
    try:
        yield
    finally:
        _SESSION.reset(token)


class Ledger:
    def __init__(self, root: Path, via: str = "cli", agent: Optional[str] = None) -> None:
        """`via` is the surface making the calls (cli, sdk, mcp). `agent` fixes the actor to an agent for a whole session,
        which is how the MCP server records every call it serves; nothing here lets a caller name a person."""
        self.root = root.resolve()
        self.dir = self.root / DIR
        self.via = via
        self._agent = agent
        self._depth = 0
        self._hashes: Optional[dict[tuple[str, str], dict]] = None
        self._tamper: Optional[list[dict]] = None

    # ------------------------------------------------------------ opening

    @classmethod
    def open(cls, start: Optional[Path] = None, via: Optional[str] = None, agent: Optional[str] = None) -> "Ledger":
        via, agent = _session(via, agent)
        root = find_root(start)
        if root is None:
            raise RBError("E_NO_INVESTIGATION")
        led = cls(root, via=via, agent=agent)
        led.actor()          # a malformed RB_ACTOR is reported on the first command, read or write
        led.investigation    # validates the root file now rather than halfway through a command
        return led

    @classmethod
    def init(cls, root: Path, title: str, id: Optional[str] = None, via: Optional[str] = None, agent: Optional[str] = None) -> "Ledger":
        via, agent = _session(via, agent)
        existing = find_root(root)
        if existing is not None:
            raise RBError("E_INVESTIGATION_EXISTS", message=f"{existing / DIR} already holds an investigation.")
        led = cls(root, via=via, agent=agent)
        who = led.actor()
        slug = id or (re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "investigation")
        inv = _build(Investigation, id=slug, title=title, created_by=who.id, created_at=now(), via=via)
        led.dir.mkdir(parents=True, exist_ok=True)
        with led._locked():
            led._write(led.dir / ".gitignore", GITIGNORE)
            led._write(led.dir / ".gitattributes", "log.jsonl merge=union\n")
            body = _dump(inv)
            sha = led._write(led.dir / "investigation.json", body)
            led._stash(body)
            led._log(who, "init", "investigation", inv.id, sha256=sha, title=title)
        return led

    def actor(self) -> Actor:
        if self._agent:
            return Actor(f"agent:{self._agent}", f"session:{self.via}", self._agent)
        return actor_mod.current(self.root)

    @property
    def investigation(self) -> Investigation:
        return self._read(self.dir / "investigation.json", Investigation)

    # ------------------------------------------------------------ files, the lock, the log

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """One writer at a time across processes. Re-entrant within a Ledger."""
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
            self._hashes, self._tamper = None, None
            yield
        finally:
            self._depth = 0
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write(self, path: Path, text: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, 0o666 & ~_UMASK)      # mkstemp makes it 0600; .rb/ is shared like the rest of the checkout
            os.replace(tmp, path)
        except OSError as exc:
            raise RBError("E_WRITE_FAILED", message=f"Could not write {path}: {exc}")
        return sha256_bytes(text.encode("utf-8"))

    def _read(self, path: Path, model: type[BaseModel]) -> Any:
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RBError("E_OBJECT_NOT_FOUND", message=f"{path.relative_to(self.root)} does not exist.")
        except (ValidationError, ValueError) as exc:
            problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()] if isinstance(exc, ValidationError) else [str(exc)]
            raise RBError("E_STATE_CORRUPT", message=f"{path.relative_to(self.root)} is not valid.", problems=problems)

    def _path(self, kind: str, id: str) -> Path:
        if kind == "investigation":
            return self.dir / "investigation.json"
        return self.dir / KINDS[kind][0] / f"{id}.json"

    def _commit(self, who: Actor, op: str, kind: str, obj: BaseModel, **detail: Any) -> None:
        """Write an object and log it with its hash and body. Every write of an object goes through here, and none
        writes over a file changed outside rb: that would log someone else's edit as rb's own."""
        why = self.edited_outside(kind, obj.id)  # type: ignore[attr-defined]
        if why:
            raise RBError("E_STATE_EDITED", message=f"{why}; rb will not write over it.")
        body = _dump(obj)
        sha = self._write(self._path(kind, obj.id), body)  # type: ignore[attr-defined]
        self._stash(body)
        self._log(who, op, kind, obj.id, sha256=sha, **detail)  # type: ignore[attr-defined]

    def _stash(self, body: str) -> None:
        """Keep a copy of what rb wrote, by its hash, so rb doctor --restore can put it back exactly. The store is local
        (gitignored): what came from elsewhere is found in git history instead."""
        data = body.encode("utf-8")
        sha = sha256_bytes(data)
        p = self.dir / "objects" / sha[:2] / f"{sha}.gz"
        if p.exists():
            return
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(gzip.compress(data, mtime=0))
            os.replace(tmp, p)
            ignore = self.dir / ".gitignore"
            if ignore.exists() and "objects/" not in ignore.read_text(encoding="utf-8").split():
                with ignore.open("a", encoding="utf-8") as f:
                    f.write("objects/\n")
        except OSError:
            pass            # a copy that could not be kept only means --restore falls back to git

    def _body(self, kind: str, id: str, sha: str) -> Optional[str]:
        """What rb wrote with this hash: from the local store, else from the file's git history."""
        p = self.dir / "objects" / sha[:2] / f"{sha}.gz"
        if p.exists():
            try:
                return gzip.decompress(p.read_bytes()).decode("utf-8")
            except (OSError, EOFError, ValueError):
                pass
        rel = str(self._path(kind, id).relative_to(self.root))
        try:
            commits = subprocess.run(["git", "log", "--format=%H", "--", rel], cwd=self.root, capture_output=True, text=True, timeout=30).stdout.split()
            for c in commits:
                blob = subprocess.run(["git", "show", f"{c}:{rel}"], cwd=self.root, capture_output=True, timeout=30).stdout
                if blob and sha256_bytes(blob) == sha:
                    return blob.decode("utf-8")
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def _log(self, who: Actor, op: str, kind: str, id: str, sha256: Optional[str] = None, **detail: Any) -> None:
        entry: dict[str, Any] = {"at": now(), "actor": who.id, "actor_via": who.via, "via": self.via, "op": op, "kind": kind, "id": id}
        if who.ignored:
            entry["ignored_rb_actor"] = who.ignored
        if sha256:
            entry["sha256"] = sha256
        if detail:
            entry["detail"] = detail
        try:
            with (self.dir / "log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            raise RBError("E_WRITE_FAILED", message=f"Could not append to {self.dir / 'log.jsonl'}: {exc}")
        if self._hashes is not None and (sha256 or op == "adopt_delete"):
            self._hashes[(kind, id)] = entry
        self._tamper = None

    def log_entries(self, last: Optional[int] = None, about: Optional[str] = None) -> list[dict]:
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
                raise RBError("E_STATE_CORRUPT", message=f".rb/log.jsonl line {n} is not a log entry (a merge conflict?).", problems=[line[:200]])
            rows.append(row)
        rows.sort(key=lambda r: str(r.get("at", "")))   # a union merge interleaves two branches' lines; order by time
        if about:
            exp_id, setting = parse_address(about)
            rows = [r for r in rows if r.get("id") == exp_id and (setting is None or (r.get("detail") or {}).get("setting") == setting)]
        return rows[-last:] if last else rows

    def last_writes(self) -> dict[tuple[str, str], dict]:
        """The last log entry that wrote each object (or recorded a person adopting its deletion)."""
        if self._hashes is None:
            last: dict[tuple[str, str], dict] = {}
            for r in self.log_entries():
                if r.get("kind") and r.get("id") and (r.get("sha256") or r.get("op") == "adopt_delete"):
                    last[(r["kind"], r["id"])] = r
            self._hashes = last
        return self._hashes

    def edited_outside(self, kind: str, id: str) -> Optional[str]:
        """Why the object's file is not what rb last wrote, or None: edited, deleted, or never written by rb."""
        entry = self.last_writes().get((kind, id))
        path = self._path(kind, id)
        exists = path.exists()
        if entry is None:
            return f"{kind} {id} was not written by rb: log.jsonl has no record of it" if exists else None
        if entry.get("op") == "adopt_delete":
            return f"{kind} {id} was deleted (a person adopted that) and its file is back" if exists else None
        if not exists:
            return f"{kind} {id} was deleted outside rb"
        return None if sha256_bytes(path.read_bytes()) == entry["sha256"] else f"{kind} {id} differs from what rb wrote: it was edited outside rb"

    def tampered(self) -> list[dict]:
        """Every object whose file is not what rb last wrote. While any is, no claim is established and every gate fails."""
        if self._tamper is None:
            seen = set(self.last_writes())
            for kind, (d, _, _) in KINDS.items():
                p = self.dir / d
                if p.is_dir():
                    seen |= {(kind, f.name[:-5]) for f in p.glob("*.json")}
            out = []
            for kind, id in sorted(seen):
                why = self.edited_outside(kind, id)
                if why:
                    out.append({"kind": kind, "id": id, "path": str(self._path(kind, id).relative_to(self.root)), "why": why})
            self._tamper = out
        return self._tamper

    def restore(self) -> list[dict]:
        """Put back what rb last wrote for every object changed outside rb. A file rb never wrote is moved aside, not
        deleted. Anyone may restore: it only returns the state to rb's own record."""
        who = self.actor()
        done = []
        with self._locked():
            for t in self.tampered():
                entry = self.last_writes().get((t["kind"], t["id"]))
                path = self._path(t["kind"], t["id"])
                if entry is None or entry.get("op") == "adopt_delete":
                    aside = path.with_name(path.name + ".outside-rb")
                    os.replace(path, aside)
                    self._log(who, "restore", t["kind"], t["id"], moved_aside=str(aside.relative_to(self.root)))
                    done.append({**t, "done": f"moved aside to {aside.relative_to(self.root)}"})
                else:
                    body = self._body(t["kind"], t["id"], entry["sha256"])
                    if body is None:
                        done.append({**t, "done": "not restored: no copy of rb's last write here or in git history"})
                        continue
                    sha = self._write(path, body)
                    self._log(who, "restore", t["kind"], t["id"], sha256=sha)
                    done.append({**t, "done": "restored to what rb last wrote"})
        return done

    def adopt(self, why: str, handoff: Optional[str] = None) -> list[dict]:
        """A person accepts every change made outside rb as it stands: the files become rb's record, and a deletion is
        recorded as one. A file that does not parse is refused."""
        who = self.actor()
        self._person(who, "Adopting changes made outside rb", handoff)
        done = []
        with self._locked():
            for t in self.tampered():
                path = self._path(t["kind"], t["id"])
                if not path.exists():
                    self._log(who, "adopt_delete", t["kind"], t["id"], why=why)
                    done.append({**t, "done": "deletion adopted"})
                    continue
                model = Investigation if t["kind"] == "investigation" else KINDS[t["kind"]][2]
                self._read(path, model)            # E_STATE_CORRUPT if it is not a valid object
                body = path.read_text(encoding="utf-8")
                self._stash(body)
                self._log(who, "adopt", t["kind"], t["id"], sha256=sha256_bytes(body.encode("utf-8")), why=why)
                done.append({**t, "done": "adopted as it stands"})
        return done

    def all(self, kind: str) -> list[Any]:
        d = self.dir / KINDS[kind][0]
        if not d.is_dir():
            return []
        items = [self._read(p, KINDS[kind][2]) for p in d.glob("*.json")]
        return sorted(items, key=lambda o: (getattr(o, "created_at", "") or getattr(o, "at", ""), o.id))

    def live(self, kind: str) -> list[Any]:
        return [o for o in self.all(kind) if getattr(o, "retracted", None) is None]

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

    def resolve_setting(self, address: str) -> tuple[Experiment, str, Setting]:
        """`e1/lr`, `e1/int8.lr` or `e1.lr` -> (experiment, full setting name, setting)."""
        exp_id, name = parse_address(address)
        if name is None and "." in address:
            head, _, tail = address.partition(".")
            if self.kind_of(head) == "experiment":
                exp_id, name = head, tail
        if name is None:
            raise RBError("E_OBJECT_NOT_FOUND", message=f"{address!r} is not a setting address; write <experiment>/<setting>, e.g. e1/lr.")
        exp = self.load("experiment", exp_id)
        s = exp.lookup(name)
        if s is None:   # a setting only some variants have (int8.calib) is addressed by its bare name, as its confound is
            s = next((v.setting(name) for v in exp.live_variants() if v.setting(name) is not None and v.setting(name).retracted is None), None)
        if s is None:
            raise RBError("E_OBJECT_NOT_FOUND", message=f"Experiment {exp.id} has no setting {name!r}.")
        return exp, name, s

    def _new_id(self, kind: str, wanted: Optional[str] = None) -> str:
        """Called under the lock. A chosen id is kept; otherwise the kind's prefix and four random characters, so two
        branches never mint the same id."""
        if wanted:
            if not re.fullmatch(ID_PATTERN, wanted):
                raise RBError("E_OBJECT_INVALID", message=f"Ids are letters, digits, '_', '.' and '-', up to 80 characters (got {wanted!r}).")
            if self.kind_of(wanted) is not None:
                raise RBError("E_OBJECT_INVALID", message=f"{wanted!r} is already taken in this investigation.")
            return wanted
        prefix = KINDS[kind][1]
        while True:
            tail = base64.b32encode(secrets.token_bytes(5)).decode("ascii").lower()[:4]
            candidate = f"{prefix}{tail}"
            if not tail.isdigit() and self.kind_of(candidate) is None:
                return candidate

    def _require(self, kind: str, id: Optional[str]) -> None:
        if id is not None and (not re.fullmatch(ID_PATTERN, id) or not self._path(kind, id).exists()):
            raise RBError("E_OBJECT_NOT_FOUND", message=f"No {kind} {id!r} in this investigation.")

    def _person(self, who: Actor, what: str, handoff: Optional[str] = None) -> None:
        if not who.is_person:
            ignored = f"; RB_ACTOR={who.ignored} is ignored inside {actor_mod.runtime_label(who.runtime)}" if who.ignored and who.runtime else ""
            raise RBError("E_HUMAN_ONLY", message=f"{what} is a person's call, and rb records you as {who.id} ({who.via}{ignored}).",
                          extra={"handoff": {"who": "person", "command": handoff}} if handoff else {})

    # ------------------------------------------------------------ questions, hypotheses, assumptions, metrics

    def add_question(self, text: str, id: Optional[str] = None) -> Question:
        who = self.actor()
        with self._locked():
            q = _build(Question, id=self._new_id("question", id), text=text, created_by=who.id, created_at=now(), via=self.via)
            self._commit(who, "add", "question", q)
        return q

    def add_hypothesis(self, statement: str, why: str = "", question: Optional[str] = None, id: Optional[str] = None) -> Hypothesis:
        who = self.actor()
        with self._locked():
            self._require("question", question)
            h = _build(Hypothesis, id=self._new_id("hypothesis", id), statement=statement, why=why, question=question,
                       created_by=who.id, created_at=now(), via=self.via)
            self._commit(who, "add", "hypothesis", h)
        return h

    def add_assumption(self, text: str, applies_to: Iterable[str] = (), id: Optional[str] = None) -> Assumption:
        who = self.actor()
        applies = list(applies_to)
        with self._locked():
            for e in applies:
                self._require("experiment", e)
            a = _build(Assumption, id=self._new_id("assumption", id), text=text, applies_to=applies, created_by=who.id, created_at=now(), via=self.via)
            self._commit(who, "add", "assumption", a)
        return a

    def add_metric(self, name: str, unit: str = "", direction: str = "none", aliases: Iterable[str] = (), description: str = "") -> Metric:
        who = self.actor()
        with self._locked():
            if self.kind_of(name) == "metric":
                raise RBError("E_OBJECT_INVALID", message=f"Metric {name!r} is already in the catalogue.")
            taken = {a: m.id for m in self.live("metric") for a in [m.id, *m.aliases]}
            clash = [a for a in [name, *aliases] if a in taken]
            if clash:
                raise RBError("E_OBJECT_INVALID", message=f"{', '.join(clash)} already names metric {taken[clash[0]]}.")
            m = _build(Metric, id=self._new_id("metric", name), unit=unit, direction=direction, aliases=list(aliases), description=description,
                       created_by=who.id, created_at=now(), via=self.via)
            self._commit(who, "add", "metric", m)
        return m

    # ------------------------------------------------------------ experiments, variants, settings

    def add_experiment(self, title: str, id: Optional[str] = None, hypotheses: Iterable[str] = (), baseline: Optional[str] = None,
                       candidates: Iterable[str] = (), varies: Iterable[str] = (), note: str = "", like: Optional[str] = None) -> Experiment:
        who = self.actor()
        hyps, cands, vary = list(hypotheses), list(candidates), list(varies)
        with self._locked():
            for h in hyps:
                self._require("hypothesis", h)
            for v in vary:
                if not re.fullmatch(SETTING_PATTERN, v):
                    raise RBError("E_OBJECT_INVALID", message=f"--varies takes setting names (got {v!r}).")
            at = now()
            variants = []
            for name, role in ([(baseline, "baseline")] if baseline else []) + [(c, "candidate") for c in cands]:
                variants.append(_build(Variant, name=name, role=role, created_by=who.id, created_at=at, via=self.via))
            settings: list[Setting] = []
            if like:
                src = self.load("experiment", like)
                settings = [s.model_copy(update={"status": "unknown" if s.per_run else ("provisional" if s.value is not None else "unknown"),
                                                 "source": s.source.model_copy(update={"resolved": None}) if s.source else None,
                                                 "conflict": None, "set_by": who.id, "set_at": at, "via": self.via})
                            for s in src.settings if s.retracted is None]
                vary = vary or list(src.varies)
            e = _build(Experiment, id=self._new_id("experiment", id), title=title, hypotheses=hyps, varies=vary, variants=variants,
                       settings=settings, note=note, created_by=who.id, created_at=at, via=self.via)
            self._commit(who, "add", "experiment", e, variants=[v.name for v in variants], like=like)
            for h in hyps:
                hyp = self.load("hypothesis", h)
                if hyp.status == "proposed":
                    hyp.status = "active"
                    self._commit(who, "activate", "hypothesis", hyp, by_experiment=e.id)
        return e

    def add_variant(self, experiment_id: str, name: str, role: str, note: str = "", amend: Optional[str] = None) -> Variant:
        who = self.actor()
        if amend:
            self._person(who, "Amending a frozen experiment")
        with self._locked():
            exp = self.load("experiment", experiment_id)
            self._writable(exp)
            if not re.fullmatch(VARIANT_PATTERN, name):
                raise RBError("E_OBJECT_INVALID", message=f"Variant names are letters, digits, '_' and '-' (no '.'), up to 40 (got {name!r}).")
            if any(v.name == name for v in exp.variants):
                raise RBError("E_OBJECT_INVALID", message=f"{exp.id} already has a variant {name!r}.")
            if role == "baseline" and exp.by_role("baseline"):
                raise RBError("E_OBJECT_INVALID", message=f"{exp.id} already has a baseline ({exp.by_role('baseline')[0].name}); an experiment compares against one.")
            before = self.freeze_sha(exp)
            v = _build(Variant, name=name, role=role, note=note, created_by=who.id, created_at=now(), via=self.via)
            exp.variants.append(v)
            amended = self._guard(exp, before, self.freeze_sha(exp), amend, who, f"new variant {name} ({role})")
            self._commit(who, "add_variant", "experiment", exp, variant=name, role=role, **amended)
        return v

    def declare_varies(self, experiment_id: str, names: Iterable[str], amend: Optional[str] = None) -> tuple[Experiment, list[str]]:
        """Declare settings the variants differ in on purpose: a difference in one is the question, not a confound. It
        changes the spec, so evidence attached before it stops counting, and a frozen experiment needs a person's amendment."""
        who = self.actor()
        if amend:
            self._person(who, "Amending a frozen experiment")
        names = list(dict.fromkeys(names))
        for n in names:
            if not re.fullmatch(SETTING_PATTERN, n):
                raise RBError("E_OBJECT_INVALID", message=f"rb spec vary takes setting names (got {n!r}).")
        with self._locked():
            exp = self.load("experiment", experiment_id)
            self._writable(exp)
            new = [n for n in names if n not in exp.varies]
            if not new:
                raise RBError("E_OBJECT_INVALID", message=f"{exp.id} already varies {', '.join(names)}.")
            before = self.freeze_sha(exp)
            exp.varies = [*exp.varies, *new]
            amended = self._guard(exp, before, self.freeze_sha(exp), amend, who, f"varies {', '.join(new)} on purpose")
            self._commit(who, "spec_vary", "experiment", exp, varies=new, **amended)
        return exp, new

    def _writable(self, exp: Experiment) -> None:
        if exp.retracted is not None:
            raise RBError("E_OBJECT_INVALID", message=f"Experiment {exp.id} was retracted ({exp.retracted.why}); it takes no more settings, claims or evidence.")

    def spec_sha(self, exp: Experiment) -> str:
        """The settings evidence is produced under: every variant and its role, every setting's value and whether it is
        required. Status and source are not in it (verifying changes nothing); per-run settings have no value in it."""
        return _sha({
            "variants": sorted((v.name, v.role) for v in exp.live_variants()),
            "varies": sorted(exp.varies),
            "settings": sorted((name, _canonical(s.value), s.required, s.per_run) for name, s in exp.all_settings()),
        })

    def freeze_sha(self, exp: Experiment, claims: Optional[list[Claim]] = None) -> str:
        """What freezing locks: the spec, where each setting's value comes from and what a cited source used, every claim's
        criterion, and the catalogue entries the claims read (so a metric cannot be redefined under a frozen claim)."""
        claims = self.claims_of(exp.id) if claims is None else claims
        live = [c for c in claims if c.retracted is None]
        return _sha({"spec": self.spec_sha(exp),
                     "claims": sorted((c.id, c.metric, c.comparator, c.target, c.tolerance, c.over, c.min_n, c.noise) for c in live),
                     "sources": sorted((name, s.source.label() if s.source else None, _canonical(s.cited.value) if s.cited else None,
                                        s.cited.source.label() if s.cited and s.cited.source else None) for name, s in exp.all_settings()),
                     "catalogue": self._catalogue_read_by(live)})

    def _catalogue_read_by(self, claims: list[Claim]) -> list:
        """The catalogue entries whose names a claim's metric could read, retracted ones included."""
        names = {c.metric.split(".")[-1] for c in claims}
        return sorted((m.id, sorted(m.aliases), m.direction, m.retracted is not None) for m in self.all("metric") if names & {m.id, *m.aliases})

    def recorded_sha(self, exp: Experiment) -> Optional[str]:
        """The hash the freeze record says the experiment has: the last amendment's, else the freeze's."""
        if exp.frozen is None:
            return None
        return exp.amendments[-1].after if exp.amendments else exp.frozen.sha256

    def spec_drift(self, exp: Experiment) -> Optional[str]:
        if exp.frozen is None:
            return None
        expected = self.recorded_sha(exp) or ""
        if self.freeze_sha(exp) != expected:
            return f"{exp.id} no longer matches its freeze record ({expected[:12]}): its spec, a source, a cited value or a metric its claims read changed without an amendment"
        return None

    def settings_snapshot(self, exp: Experiment) -> dict[str, Any]:
        return {name: s.value for name, s in exp.all_settings() if not s.per_run}

    def _guard(self, exp: Experiment, before: str, after: str, amend: Optional[str], who: Actor, change: str) -> dict:
        """After a change is applied in memory, before it is saved. Returns log detail for an amendment it recorded."""
        changes_frozen = exp.frozen is not None and before != after
        if not changes_frozen:
            if amend:
                why = "the experiment is not frozen" if exp.frozen is None else "this does not change the frozen spec"
                raise RBError("E_OBJECT_INVALID", message=f"--amend has nothing to amend: {why}. Run it without --amend.")
            return {}
        if not amend:
            raise RBError("E_FROZEN", message=f"Experiment {exp.id} was frozen at {exp.frozen.at} ({exp.frozen.sha256[:12]}); this changes it ({change}).")
        self._person(who, "Amending a frozen experiment")
        recorded = self.recorded_sha(exp) or before
        if before != recorded:        # an earlier unamended change is adopted by this amendment, and says so
            change += "; also adopts changes made without an amendment since the last record"
        exp.amendments.append(_build(Amendment, at=now(), by=who.id, why=amend, change=change[:400], before=recorded, after=after))
        return {"amendment": {"why": amend, "change": change, "before": recorded, "after": after}}

    def set_setting(self, experiment_id: str, name: str, value: Any = None, *, unknown: bool = False, source: Optional[Source] = None,
                    required: Optional[bool] = None, per_run: Optional[bool] = None, cited: Optional[Cited] = None, note: Optional[str] = None,
                    amend: Optional[str] = None, verify: bool = True) -> tuple[Setting, Optional[dict]]:
        """Propose a setting's value (`provisional`), record it as `unknown`, or declare it per-run. The value is then
        verified against its source when the source can be checked (unless verify=False). `<variant>.<name>` sets a
        variant's own value. Returns the setting and the verification row."""
        who = self.actor()
        if amend:
            self._person(who, "Amending a frozen experiment")
        if unknown and value is not None:
            raise RBError("E_OBJECT_INVALID", message="A setting is either unknown or has a value, not both.")
        if source is not None and source.resolved is not None:
            source = source.model_copy(update={"resolved": None})
        with self._locked():
            exp = self.load("experiment", experiment_id)
            self._writable(exp)
            variant, bare = exp.split(name)
            if not re.fullmatch(SETTING_PATTERN, bare):
                raise RBError("E_OBJECT_INVALID", message=f"Setting names are letters, digits and _.:- (got {bare!r}).")
            holder = variant.settings if variant is not None else exp.settings
            old = next((s for s in holder if s.name == bare and s.retracted is None), None)
            before = self.freeze_sha(exp)
            is_per_run = per_run if per_run is not None else (old.per_run if old else False)
            if is_per_run:
                if value is not None:
                    raise RBError("E_OBJECT_INVALID", message=f"{bare} is per-run: each piece of evidence gives its own value with --set {bare}=<value>.")
                unknown = True
            elif not unknown and value is None:
                if old is None:
                    raise RBError("E_OBJECT_INVALID", message=f"Give {name} a value, record it as unknown, or declare it --per-run.")
                value, unknown = old.value, old.status == "unknown"
            if source is not None and source.kind == "file" and source.line and not source.key and structured_kind(str(source.path)) == "yaml":
                source = self._yaml_line_to_key(source, bare)
            same_value = old is not None and not unknown and _same_value(old.value, value)
            new_source = source if source is not None else (old.source if same_value else None)
            keep = same_value and old.status == "verified" and (source is None or _same_source(old.source, source)) and old.conflict is None
            if keep:
                status, new_source = "verified", old.source
            else:
                status = "unknown" if unknown else "provisional"
                if new_source is not None and new_source.resolved is not None:
                    new_source = new_source.model_copy(update={"resolved": None})
            new = _build(Setting, name=bare, value=None if unknown else value, status=status,
                         required=(old.required if old else True) if required is None else required, per_run=is_per_run,
                         source=new_source, cited=cited if cited is not None else (old.cited if old else None),
                         note=(old.note if note is None and old else (note or "")), set_by=who.id, set_at=now(), via=self.via)
            if old is not None:
                holder[holder.index(old)] = new
            else:
                holder.append(new)
            change = _describe_change(name, old, new)
            amended = self._guard(exp, before, self.freeze_sha(exp), amend, who, change)
            self._commit(who, "spec_set", "experiment", exp, setting=name, change=change, value=new.value, status=new.status, required=new.required,
                         source=new_source.label() if new_source else None, **amended)
            row = None
            if verify and new_source is not None and new_source.checkable() and new.status == "provisional":
                row = self._verify_one(exp, name, new, who)
                self._commit(who, "spec_verify", "experiment", exp, setting=name, outcome=row["after"], **({"reason": row["reason"]} if not row["ok"] else {}))
                new = exp.lookup(name)
        return new, row

    def _yaml_line_to_key(self, source: Source, name: str) -> Source:
        """A line of a YAML file is turned into the key path on that line, so moving lines never matters afterwards, but
        only when that key is the setting's own. A line holding the value under another key stays a line, and fails the
        check that the line names the setting."""
        from .sources import key_names, read_file
        try:
            data, _ = read_file(self.root, str(source.path), source.commit)
        except Unresolved:
            return source
        key = yaml_key_at(data.decode("utf-8", errors="replace"), int(source.line or 0))
        if key is None or not key_names(key, name, source.term):
            return source
        return source.model_copy(update={"key": key, "line": None})

    def _verify_one(self, exp: Experiment, name: str, s: Setting, who: Actor) -> dict:
        row: dict[str, Any] = {"setting": name, "value": s.value, "before": s.status}
        if s.per_run:
            row.update(after=s.status, ok=True, skipped=True, reason="per-run: each piece of evidence gives its own value")
            return row
        if s.value is None:
            row.update(after=s.status, ok=False, code="E_SOURCE_UNRESOLVED", reason="the setting is unknown; propose a value with its source first")
            return row
        if s.status == "inherited":
            row.update(after=s.status, ok=False, code="E_SOURCE_UNRESOLVED", reason="an inherited setting is verified by setting it with a source here")
            return row
        if s.source is None:
            row.update(after=s.status, ok=True, skipped=True, code="E_SOURCE_UNVERIFIABLE", reason="no source to check")
            return row
        if not s.source.checkable():
            row.update(after=s.status, ok=True, skipped=True, code="E_SOURCE_UNVERIFIABLE",
                       reason=f"not checkable ({s.source.kind}); it stays provisional. Point --source at the file or run that sets it")
            return row
        try:
            res = resolve(s.source, s.value, self.root, name, previous=s.source.resolved)
            s.source = s.source.model_copy(update={"resolved": res})
            s.status = "verified"
            s.conflict = None
            row.update(after="verified", ok=True, read=res.text, line=res.line, commit=res.commit, sha256=res.sha256, where=s.source.label())
        except Unresolved as u:
            if u.differs:
                # the source states a different value: a conflict, which re-verifying never clears
                s.conflict = Conflict(at=now(), read=u.found, text=u.reason[:400])
            if u.differs or s.status == "verified":
                s.status = "provisional"
                s.source = s.source.model_copy(update={"resolved": None})
            row.update(after=s.status, ok=False, code=u.code, reason=u.reason, candidates=u.candidates, conflict=s.conflict is not None)
        return row

    def verify_settings(self, experiment_id: str, names: Optional[Iterable[str]] = None) -> list[dict]:
        """Check each named setting's source (every setting with a source when none are named). rb decides the outcome;
        the caller only asks. A source rb cannot check (a url, a note) is reported and does not fail the check."""
        who = self.actor()
        wanted = list(names or [])
        with self._locked():
            exp = self.load("experiment", experiment_id)
            self._writable(exp)
            for n in wanted:
                if exp.lookup(n) is None:
                    raise RBError("E_OBJECT_NOT_FOUND", message=f"Experiment {exp.id} has no setting {n!r}.")
            targets = [(n, exp.lookup(n)) for n in wanted] if wanted else [(n, s) for n, s in exp.all_settings() if s.source is not None]
            rows = [self._verify_one(exp, n, s, who) for n, s in targets if s is not None]
            if rows:
                self._commit(who, "spec_verify", "experiment", exp, results=[{"setting": r["setting"], "after": r["after"], "ok": r["ok"]} for r in rows])
        return rows

    def freeze(self, experiment_id: str, why: str = "", handoff: Optional[str] = None, amend: bool = False) -> Freeze:
        who = self.actor()
        self._person(who, "Amending a frozen experiment" if amend else "Freezing an experiment", handoff)
        with self._locked():
            exp = self.load("experiment", experiment_id)
            self._writable(exp)
            if amend:
                if exp.frozen is None:
                    raise RBError("E_OBJECT_INVALID", message=f"{exp.id} is not frozen; freeze it without --amend.")
                recorded, current = self.recorded_sha(exp), self.freeze_sha(exp)
                if recorded == current:
                    raise RBError("E_OBJECT_INVALID", message=f"{exp.id} matches its freeze record; there is nothing to adopt.")
                exp.amendments.append(_build(Amendment, at=now(), by=who.id, why=why, change="adopted the experiment as it is now", before=recorded or "", after=current))
                self._commit(who, "amend", "experiment", exp, why=why, before=recorded, after=current)
                return exp.frozen
            if exp.frozen is not None:
                raise RBError("E_FROZEN", message=f"Experiment {exp.id} is already frozen ({exp.frozen.at}).",
                              fix="Change it with --amend --why \"<reason>\" on the command that changes it; the amendment is recorded.")
            before = [e.id for e in self.live("evidence") if e.experiment == exp.id]
            exp.frozen = _build(Freeze, sha256=self.freeze_sha(exp), at=now(), by=who.id, why=why, after_evidence=before)
            self._commit(who, "freeze", "experiment", exp, sha256_spec=exp.frozen.sha256, why=why, after_evidence=before)
        return exp.frozen

    # ------------------------------------------------------------ claims

    def claims_of(self, experiment_id: str) -> list[Claim]:
        return [c for c in self.all("claim") if c.experiment == experiment_id]

    def add_claim(self, statement: str, *, metric: str, comparator: str, target: float, tolerance: Optional[float] = None,
                  experiment: Optional[str] = None, hypothesis: Optional[str] = None, source: Optional[Source] = None,
                  over: str = "each", min_n: int = 1, noise: Optional[float] = None, note: str = "",
                  amend: Optional[str] = None, id: Optional[str] = None) -> Claim:
        """A claim's criterion is fixed once written. A claim citing a file is checked now: the file must state the
        target, or the claim is refused; a url or a note is recorded and the verdict says it was not checked."""
        who = self.actor()
        if amend:
            self._person(who, "Amending a frozen experiment")
        if source is not None and source.resolved is not None:
            source = source.model_copy(update={"resolved": None})
        with self._locked():
            self._require("hypothesis", hypothesis)
            claim = _build(Claim, id=self._new_id("claim", id), statement=statement, experiment=experiment, hypothesis=hypothesis, metric=metric,
                           comparator=comparator, target=target, tolerance=tolerance, over=over, min_n=min_n, noise=noise,
                           origin="cited" if source is not None else "own", source=source, note=note, created_by=who.id, created_at=now(), via=self.via)
            if source is not None and source.checkable():
                try:
                    claim.source = source.model_copy(update={"resolved": resolve(source, claim.target, self.root, metric.split(".")[-1], named=False)})
                except Unresolved as u:
                    raise RBError(u.code, message=f"The claim's source does not state its target {claim.target:g}: {u.reason}",
                                  fix="Point --source/--quote at the text that states the number, or correct the target.")
            amended: dict = {}
            if experiment is not None:
                exp = self.load("experiment", experiment)
                self._writable(exp)
                before = self.freeze_sha(exp)
                after = self.freeze_sha(exp, [*self.claims_of(exp.id), claim])
                amended = self._guard(exp, before, after, amend, who, f"new claim {claim.id}: {claim.criterion()}")
                if amended:
                    self._commit(who, "amend", "experiment", exp, **amended)
            elif amend:
                raise RBError("E_OBJECT_INVALID", message="--amend has nothing to amend: the claim is not on an experiment.")
            self._commit(who, "add", "claim", claim, criterion=claim.criterion(), experiment=experiment, **amended)
        return claim

    # ------------------------------------------------------------ evidence

    def attach_evidence(self, experiment_id: str, *, metrics: Optional[dict[str, float]] = None, variant: Optional[str] = None,
                        run: Optional[dict] = None, files: Iterable[str] = (), links: Optional[dict[str, str]] = None,
                        command: Optional[str] = None, per_run: Optional[dict[str, Any]] = None, config: Optional[str] = None,
                        commit: Optional[str] = None, basis: str = "typed", again: Optional[str] = None, note: str = "") -> dict:
        """Attach numbers to an experiment. Returns {evidence, duplicate_of, warning}: when identical evidence is already
        attached, nothing is written and duplicate_of names it (unless `again` gives a reason for a second copy)."""
        who = self.actor()
        values: dict[str, float] = {}
        files = list(files)
        with self._locked():
            exp = self.load("experiment", experiment_id)
            self._writable(exp)
            if variant is not None and exp.variant(variant) is None:
                raise RBError("E_OBJECT_NOT_FOUND", message=f"{exp.id} has no variant {variant!r} (it has: {', '.join(v.name for v in exp.live_variants()) or 'none'}).")
            for k, x in (metrics or {}).items():
                values[self._metric_key(exp, k, variant)] = x
            run_record, synthetic = None, False
            if run is not None:
                for k, x in run["metrics"].items():
                    key = self._run_metric_key(exp, k)
                    if key in values:
                        raise RBError("E_OBJECT_INVALID", message=f"{key} is given twice: by the run and by --metric.")
                    values[key] = x
                run_record, synthetic, basis = run["record"], run["synthetic"], "run"
                if run.get("file"):
                    files.append(run["file"])
            if not values:
                raise RBError("E_OBJECT_INVALID", message="Evidence needs at least one number: NAME=VALUE, --from file.json, or --run <run>.")
            given = dict(per_run or {})
            for k in given:
                s = exp.lookup(k)
                if s is None or not s.per_run:
                    raise RBError("E_OBJECT_INVALID", message=f"--set {k}: {k} is not a per-run setting of {exp.id}. Declare it: rb spec set {exp.id} {k} --per-run.")
            missing = sorted({n for n, s in exp.all_settings() if s.per_run and s.retracted is None and n not in given and n.split(".")[-1] not in given})
            if missing:
                raise RBError("E_OBJECT_INVALID", message=f"{exp.id} declares {', '.join(missing)} per-run: give each run's own with "
                              + " ".join(f"--set {n}=<value>" for n in missing) + ". Without it, rb cannot tell a new run from the same one again.")
            if run is not None:
                self._check_run_names(exp, run)
            refs = []
            for f in files:
                try:
                    refs.append(FileRef(**file_ref(self.root, f)))
                except FileNotFoundError:
                    raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {f}")
                except OSError as exc:
                    raise RBError("E_FILE_NOT_FOUND", message=f"{f} could not be read: {exc}")
            check = self._config_check(exp, variant, config, run_record, given)
            fingerprint = _sha({"metrics": values, "per_run": given, "files": sorted(r.sha256 for r in refs),
                                "record": (run_record or {}).get("record_sha256"), "config": check.sha256 if check else None})
            dup = next((e for e in self.live("evidence") if e.experiment == exp.id and e.fingerprint == fingerprint), None)
            if dup is not None and not again:
                return {"evidence": dup, "duplicate_of": dup.id, "warning": None}
            repeat = None
            if given:
                repeat = next((e for e in self.live("evidence") if e.experiment == exp.id and e.per_run == given
                               and _variants_of(e) == set(k.split(".")[0] for k in values if "." in k)), None)
            produced = {"commit": commit, "from": "flag"} if commit else _produced_from(run_record)
            receipt = Receipt(at=now(), actor=who.id, actor_via=who.via, via=self.via, rb_version=__version__, command=command,
                              attached=git_state(self.root), produced=produced,
                              environment={"python": platform.python_version(), "platform": platform.platform()},
                              run_record=run_record, config=check)
            ev = _build(Evidence, id=self._new_id("evidence"), experiment=exp.id,
                        metrics=values, per_run=given, run=run["path"] if run is not None else None, files=[r.model_dump() for r in refs],
                        links=dict(links or {}), basis=basis, spec_sha256=self.spec_sha(exp), settings=self.settings_snapshot(exp),
                        spec_frozen=exp.frozen is not None, synthetic=synthetic, fingerprint=fingerprint, receipt=receipt.model_dump(),
                        note=(f"attached again: {again}. " if again and dup else "") + note, created_by=who.id, created_at=now(), via=self.via)
            self._commit(who, "attach", "evidence", ev, experiment=exp.id, metrics=values, per_run=given, again=bool(again and dup))
        warning = None if repeat is None else f"{ev.id} repeats {', '.join(f'{k}={show(v)}' for k, v in given.items())} of {repeat.id}; not an independent run"
        return {"evidence": ev, "duplicate_of": None, "warning": warning}

    def _metric_key(self, exp: Experiment, name: str, variant: Optional[str]) -> str:
        if variant is None or name.startswith("change."):
            return name
        head = name.split(".", 1)[0]
        if exp.variant(head) is not None or head in ROLES:
            return name
        return f"{variant}.{name}"

    def _run_metric_key(self, exp: Experiment, key: str) -> str:
        """An rb run reports baseline.<m> and candidate.<m>; on an experiment with one of each, they become the variants'."""
        head, sep, rest = key.partition(".")
        if sep and head in ("baseline", "candidate"):
            vs = exp.by_role(head)
            if len(vs) == 1:
                return f"{vs[0].name}.{rest}"
        return key

    def _check_run_names(self, exp: Experiment, run: dict) -> None:
        """A review run names its two models. When those names are this experiment's variants in the other roles, the
        numbers would land on the wrong variant: refuse rather than flip the sign of every change."""
        names = run.get("names") or {}
        for role, other in (("baseline", "candidate"), ("candidate", "baseline")):
            n = names.get(role)
            v = exp.variant(n) if n else None
            if v is not None and v.role != role:
                raise RBError("E_OBJECT_INVALID", message=f"The run's {role} is {n!r}, which is {exp.id}'s {v.role}: its numbers would be recorded "
                              f"against the wrong variant. Attach a run whose {role} is {exp.id}'s {role}.")
        if len(exp.by_role("candidate")) > 1:
            raise RBError("E_OBJECT_INVALID", message=f"{exp.id} has several candidates, and a review run compares two models: attach its numbers "
                          f"by name instead (rb evidence attach {exp.id} <baseline>.<metric>=... <candidate>.<metric>=...).")

    def _config_check(self, exp: Experiment, variant: Optional[str], config: Optional[str], run_record: Optional[dict],
                      given: Optional[dict] = None) -> Optional[ConfigCheck]:
        """Compare the spec with the run's own resolved config: did the run use what the spec says?"""
        if config is None and not run_record:
            return None
        if config is not None:
            p = Path(config) if Path(config).is_absolute() else self.root / config
            try:
                doc = load_config(p)
                data = p.read_bytes()
            except Unresolved as u:
                raise RBError("E_OBJECT_INVALID", message=f"--config {config}: {u.reason}")
            except OSError as exc:
                raise RBError("E_FILE_NOT_FOUND", message=f"--config {config}: {exc}")
            flat, label, sha = flatten(doc), config, sha256_bytes(data)
        else:
            flat, label, sha = flatten(run_record or {}), "record.json", (run_record or {}).get("record_sha256", "")
            if not flat:
                return None
        v = exp.variant(variant) if variant else None
        names = {x.name for x in exp.settings if x.retracted is None and not x.per_run}
        if v is not None:
            names |= {x.name for x in v.settings if x.retracted is None and not x.per_run}
        check = ConfigCheck(path=label, sha256=sha)
        for name in sorted(names):
            spec_value = exp.value_in(v, name) if v else (exp.setting(name).value if exp.setting(name) else None)
            if spec_value is None:
                continue
            ran, found = _config_value(flat, name)
            if not found:
                if config is not None:
                    check.absent.append(name)
                continue
            from .sources import equal
            if equal(spec_value, ran):
                check.matches.append(name)
            else:
                check.mismatches.append({"name": name, "spec": spec_value, "ran": ran})
        from .sources import equal as _eq
        for name, said in sorted((given or {}).items()):      # the per-run values given with --set, against the run's own
            ran, found = _config_value(flat, name)
            if found:
                if _eq(said, ran):
                    check.matches.append(name)
                else:
                    check.mismatches.append({"name": name, "spec": said, "ran": ran})
        return check

    # ------------------------------------------------------------ people's calls: decide, retract

    def decide(self, subject: str, outcome: str, why: str, handoff: Optional[str] = None) -> Decision:
        who = self.actor()
        self._person(who, "A decision", handoff)
        with self._locked():
            exp_id, setting = parse_address(subject)
            value, across = None, None
            if setting is not None or ("." in subject and self.kind_of(subject) is None):
                exp, name, s = self.resolve_setting(subject)
                subject = f"{exp.id}/{name}"
                kind, obj = "setting", s
                if outcome == "investigate":
                    raise RBError("E_OBJECT_INVALID", message="A setting is accepted (vouched for) or rejected; to change it, rb spec set it.")
                own_variant = "." in name and exp.variant(name.split(".", 1)[0]) is not None
                value = s.value if (own_variant or exp.setting(name) is not None) else None
                if not own_variant:     # the setting across variants: accepting it accepts how it differs, if it does
                    values = {v.name: exp.value_in(v, name) for v in exp.live_variants()}
                    across = values if len({_canonical(x) for x in values.values()}) > 1 else None
                if outcome == "accept" and s.per_run:
                    raise RBError("E_OBJECT_INVALID", message=f"{subject} is per-run: each run gives its own value, so there is none to vouch for.")
                if outcome == "accept" and value is None and across is None:
                    raise RBError("E_OBJECT_INVALID", message=f"{subject} has no value to vouch for. Set it first (rb spec set), then accept the value it has.")
            else:
                kind, obj = self.get(subject)
                if kind in ("evidence", "decision", "metric"):
                    raise RBError("E_OBJECT_INVALID", message=f"Decisions are made on claims, hypotheses, assumptions, questions, experiments and settings, not on {kind}. To stop evidence counting, retract it.")
            verdict, evidence = None, []
            if kind == "claim":
                v = self.verdict(obj)
                verdict, evidence = v.status, [o.evidence for o in v.observations if o.role == "confirmatory"]
            last = next((d for d in reversed(self.decisions_on(subject))), None)
            if last is not None and last.outcome == outcome and last.verdict == verdict and last.why == why:
                raise RBError("E_OBJECT_INVALID", message=f"{last.id} already records this decision on {subject}.")
            d = _build(Decision, id=self._new_id("decision"), subject=subject, outcome=outcome, why=why, by=who.id, at=now(), via=self.via,
                       verdict=verdict, evidence=evidence, value=value, across=across if kind == "setting" else None)
            self._commit(who, "decide", "decision", d, subject=subject, outcome=outcome, verdict=verdict, value=value)
            status_map = {
                "hypothesis": {"accept": "accepted", "reject": "rejected", "investigate": "active"},
                "assumption": {"accept": "assumed", "reject": "violated", "investigate": "open"},
                "question": {"accept": "answered", "reject": "dropped", "investigate": "open"},
            }
            if kind in status_map:
                obj.status = status_map[kind][outcome]
                self._commit(who, "status", kind, obj, status=obj.status, decision=d.id)
        return d

    def decisions_on(self, subject: str) -> list[Decision]:
        return [d for d in self.all("decision") if d.subject == subject and d.retracted is None]

    def vouch(self, exp: Experiment, name: str, s: Setting) -> Optional[Decision]:
        """A person's accept on e1/lr, still standing for the value the setting has now."""
        for d in reversed(self.decisions_on(f"{exp.id}/{name}")):
            if d.outcome == "accept":
                return d if d.value is not None and s.value is not None and _same_value(d.value, s.value) else None
            if d.outcome == "reject":
                return None
        return None

    def accepted_difference(self, exp: Experiment, name: str, values: dict[str, Any]) -> Optional[Decision]:
        """A person's accept of a setting differing between variants, still standing for the values they have now."""
        for d in reversed(self.decisions_on(f"{exp.id}/{name}")):
            if d.outcome == "accept":
                same = d.across is not None and set(d.across) == set(values) and all(_canonical(d.across[k]) == _canonical(values[k]) for k in values)
                return d if same else None
            if d.outcome == "reject":
                return None
        return None

    def rests_on(self, kind: str, obj: Any, subject: str) -> list[str]:
        """What depends on an object: while anything does, retracting it is a person's call."""
        deps = [f"decision {d.id}" for d in self.decisions_on(subject)]
        if kind == "evidence":
            for c in self.live("claim"):
                if c.experiment == obj.experiment and any(o.evidence == obj.id and o.role == "confirmatory" for o in self.verdict(c).observations):
                    deps.append(f"claim {c.id} counts it")
            exp = self.load("experiment", obj.experiment)
            if exp.frozen is not None:
                deps.append(f"experiment {exp.id} is frozen")
        elif kind == "experiment":
            if obj.frozen is not None:
                deps.append("it is frozen")
            deps += [f"evidence {e.id}" for e in self.live("evidence") if e.experiment == obj.id]
        elif kind == "claim":
            if obj.experiment:
                exp = self.load("experiment", obj.experiment)
                if exp.frozen is not None:
                    deps.append(f"experiment {exp.id} is frozen")
        elif kind == "setting":
            exp_id = subject.split("/")[0]
            if self.load("experiment", exp_id).frozen is not None:
                deps.append(f"experiment {exp_id} is frozen")
        elif kind == "metric":
            names = {obj.id, *obj.aliases}
            deps += [f"claim {c.id} reads it" for c in self.live("claim") if c.metric.split(".")[-1] in names]
        elif kind in ("hypothesis", "question"):
            deps += [f"claim {c.id}" for c in self.live("claim") if c.hypothesis == obj.id]
            deps += [f"experiment {e.id}" for e in self.live("experiment") if obj.id in e.hypotheses]
            deps += [f"hypothesis {h.id}" for h in self.live("hypothesis") if h.question == obj.id]
        return deps

    def retract(self, subject: str, why: str, handoff: Optional[str] = None) -> tuple[str, str, list[str]]:
        """Retract anything: it stays on record and stops counting. An agent may retract what it wrote while nothing rests
        on it; what a person wrote, or what something rests on, is a person's call. On a frozen experiment a retraction is
        recorded as an amendment. Returns (kind, id, what rested on it)."""
        who = self.actor()
        with self._locked():
            exp_id, name = parse_address(subject)
            if name is not None:
                exp = self.load("experiment", exp_id)
                v = exp.variant(name) if exp.lookup(name) is None else None
                if v is not None:
                    if v.retracted is not None:
                        raise RBError("E_OBJECT_INVALID", message=f"Variant {exp.id}/{name} was already retracted at {v.retracted.at}.")
                    deps = [f"{exp.id} is frozen"] if exp.frozen else []
                    why_person = deps + ([f"{v.created_by} added it"] if v.created_by.startswith("human:") else [])
                    if why_person:
                        self._person(who, f"Retracting variant {exp.id}/{name} ({'; '.join(why_person)})", handoff)
                    before = self.freeze_sha(exp)
                    v.retracted = _build(Retraction, at=now(), by=who.id, why=why)
                    amended = self._guard(exp, before, self.freeze_sha(exp), why if exp.frozen else None, who, f"variant {name} retracted")
                    self._commit(who, "retract", "experiment", exp, variant=name, why=why, **amended)
                    return "variant", f"{exp.id}/{name}", deps
                exp, name, s = self.resolve_setting(subject)
                deps = self.rests_on("setting", s, f"{exp.id}/{name}")
                why_person = deps + ([f"{s.set_by} set it"] if (s.set_by or "").startswith("human:") else [])
                if why_person:
                    self._person(who, f"Retracting {exp.id}/{name} ({'; '.join(why_person)})", handoff)
                before = self.freeze_sha(exp)
                s.retracted = _build(Retraction, at=now(), by=who.id, why=why)
                amended = self._guard(exp, before, self.freeze_sha(exp), why if exp.frozen else None, who, f"setting {name} retracted")
                self._commit(who, "retract", "experiment", exp, setting=name, why=why, **amended)
                return "setting", f"{exp.id}/{name}", deps
            kind, obj = self.get(subject)
            if getattr(obj, "retracted", None) is not None:
                raise RBError("E_OBJECT_INVALID", message=f"{subject} was already retracted at {obj.retracted.at}: {obj.retracted.why}")
            deps = [] if kind == "decision" else self.rests_on(kind, obj, subject)
            author = getattr(obj, "created_by", None) or getattr(obj, "by", None) or ""
            why_person = deps + ([f"{author} wrote it"] if author.startswith("human:") else [])
            if why_person or kind == "decision":
                self._person(who, f"Retracting {kind} {subject} ({'; '.join(why_person) or 'a decision'})", handoff)
            obj.retracted = _build(Retraction, at=now(), by=who.id, why=why)
            if kind == "claim" and obj.experiment:
                exp = self.load("experiment", obj.experiment)
                if exp.frozen is not None:     # the criteria are part of the freeze: record the change as an amendment
                    before = self.freeze_sha(exp)
                    after = self.freeze_sha(exp, [obj if c.id == obj.id else c for c in self.claims_of(exp.id)])
                    amended = self._guard(exp, before, after, why, who, f"claim {obj.id} retracted")
                    self._commit(who, "retract", kind, obj, why=why)
                    self._commit(who, "amend", "experiment", exp, **amended)
                    return kind, subject, deps
            self._commit(who, "retract", kind, obj, why=why)
            return kind, subject, deps

    # ------------------------------------------------------------ verdicts

    def metric_aliases(self) -> dict[str, str]:
        out = {}
        for m in self.live("metric"):
            out[m.id] = m.id
            for a in m.aliases:
                out[a] = m.id
        return out

    def value_for(self, exp: Experiment, metric: str, ev: Evidence, aliases: Optional[dict[str, str]] = None) -> Optional[float]:
        """The number a claim's metric reads in one piece of evidence, or None (also when the reading is ambiguous)."""
        return self.read_metric(exp, metric, ev, aliases)[0]

    def read_metric(self, exp: Experiment, metric: str, ev: Evidence, aliases: Optional[dict[str, str]] = None) -> tuple[Optional[float], Optional[str]]:
        """(the number a claim's metric reads in one piece of evidence, or None; why it cannot be read, or None).
        Two reported names that read as the same metric and disagree are ambiguous, never last-one-wins. change.<m> is
        computed from the two variants' numbers when the evidence has them, and a given change.<m> that disagrees with
        them is not read."""
        aliases = self.metric_aliases() if aliases is None else aliases
        canon: dict[str, list[tuple[str, float]]] = {}
        for k, v in ev.metrics.items():
            canon.setdefault(_canon(k, exp, aliases), []).append((k, v))

        def get(key: str) -> tuple[Optional[float], Optional[str]]:
            got = canon.get(key, [])
            if not got:
                return None, None
            if len({_canonical(x) for _, x in got}) > 1:
                return None, f"{' and '.join(k for k, _ in got)} both read as {key} and disagree"
            return got[0][1], None

        want = _canon(metric, exp, aliases)
        head, _, rest = want.partition(".")
        if head == "change" and rest:
            v2, _, m2 = rest.partition(".")
            cand = exp.variant(v2) if m2 else None
            cands = exp.by_role("candidate")
            if cand is None:
                cand, m2 = (cands[0], rest) if len(cands) == 1 else (None, rest)
            base = exp.by_role("baseline")
            if cand is not None and len(base) == 1:
                b, pb = get(f"{base[0].name}.{m2}")
                c, pc = get(f"{cand.name}.{m2}")
                if pb or pc:
                    return None, pb or pc
                if b is not None and c is not None:
                    computed = _minus(c, b)
                    given, pg = get(want)
                    if pg:
                        return None, pg
                    if given is not None and not math.isclose(given, computed, rel_tol=1e-9, abs_tol=1e-12):
                        return None, f"{want} is given as {given:g}, but {cand.name}.{m2} minus {base[0].name}.{m2} is {computed:g}"
                    return computed, None
            if cand is None and len(cands) > 1 and want in canon:
                return None, f"{want} does not say which of {', '.join(v.name for v in cands)} it is for: name one ({cands[0].name}.{rest} ...)"
            return get(want)
        v, p = get(want)
        if v is not None or p:
            return v, p
        if "." not in want or exp.variant(head) is None and head not in ROLES:
            cands = exp.by_role("candidate")
            if len(cands) == 1:
                return get(f"{cands[0].name}.{want}")
        return None, None

    def criterion_fixed_at(self, claim: Claim, exp: Experiment) -> Optional[str]:
        """When a person fixed the claim's criterion: when they wrote it, or else when they first froze or amended the
        experiment after it was written. None while no person has."""
        if claim.created_by.startswith("human:"):
            return claim.created_at
        times = []
        if exp.frozen is not None and exp.frozen.by.startswith("human:") and exp.frozen.at > claim.created_at:
            times.append(exp.frozen.at)
        times += [a.at for a in exp.amendments if a.by.startswith("human:") and a.at > claim.created_at]
        return min(times) if times else None

    def setting_caveats(self, exp: Experiment, has_origin: bool) -> list[Caveat]:
        """Everything about an experiment's settings that a verdict on it rests on."""
        out: list[Caveat] = []
        for name, s in exp.all_settings():
            subject = f"{exp.id}/{name}"
            if s.per_run:
                continue
            if s.conflict is not None:
                out.append(Caveat(code="conflict", subject=subject, blocks=True, text=f"{name}: {s.conflict.text}"))
                continue
            if s.value is None:
                out.append(Caveat(code="unknown" if s.required else "unknown_optional", subject=subject, blocks=s.required,
                                  text=f"{name} is unknown" + ("" if s.required else " (optional)")))
                continue
            if s.status == "inherited":
                if has_origin:
                    out.append(Caveat(code="inherited", subject=subject, blocks=False, text=f"{name} = {show(s.value)} is inherited from the parent investigation"))
                else:
                    out.append(Caveat(code="inherited_without_parent", subject=subject, blocks=s.required, text=f"{name} is marked inherited, but this investigation has no parent"))
                continue
            vouch = self.vouch(exp, name, s)
            rejected = next((d for d in reversed(self.decisions_on(subject)) if d.outcome == "reject"), None)
            if rejected is not None and (vouch is None or vouch.at < rejected.at) and (rejected.value is None or _same_value(rejected.value, s.value)):
                out.append(Caveat(code="rejected_setting", subject=subject, blocks=True, text=f"{name} = {show(s.value)} was rejected by {rejected.by} ({rejected.id}): {rejected.why}"))
                continue
            if s.status == "provisional":
                if vouch is not None:
                    out.append(Caveat(code="vouched", subject=subject, blocks=False, text=f"{name} = {show(s.value)} is provisional · vouched for by {vouch.by} ({vouch.id})"))
                else:
                    where = f" (source {s.source.label()}, not checkable)" if s.source is not None and not s.source.checkable() else ""
                    out.append(Caveat(code="provisional", subject=subject, blocks=s.required,
                                      text=f"{name} = {show(s.value)} is provisional: nobody checked it{where}"))
            elif s.status == "verified" and s.source is not None:
                state, why = recheck(s.source, s.value, self.root, name)
                if state in ("stale", "conflict"):
                    out.append(Caveat(code=state, subject=subject, blocks=True, text=f"{name}: {why}"))
                elif state == "moved":
                    out.append(Caveat(code="source_changed", subject=subject, blocks=False, text=f"{name}: {why}"))
            if s.cited is not None and not _same_value(s.cited.value, s.value):
                out.append(Caveat(code="cited_differs", subject=subject, blocks=False, text=f"{name} {show(s.value)} here vs {show(s.cited.value)} in the cited source"))
        out += self.confounds(exp)
        return out

    def unread_metrics(self, exp: Experiment, evidence: list[Evidence], claims: list[Claim]) -> list[str]:
        """The numbers the evidence reports that no claim reads, matched the way a verdict reads them: through the
        catalogue's aliases, role names (candidate.epe), and both variants behind change.epe."""
        aliases = self.metric_aliases()
        read: set[str] = set()
        for c in claims:
            if c.retracted is not None:
                continue
            canon = _canon(c.metric, exp, aliases)
            read.add(canon)
            head, _, rest = canon.partition(".")
            if head == "change" and rest:
                v2, _, m2 = rest.partition(".")
                base_metric = m2 if (m2 and exp.variant(v2) is not None) else rest
                read |= {f"{v.name}.{base_metric}" for v in exp.live_variants()}
        reported = {k for ev in evidence for k in ev.metrics}
        return sorted(k for k in reported if _canon(k, exp, aliases) not in read and k not in RUN_COUNTS)

    def confounds(self, exp: Experiment) -> list[Caveat]:
        """A setting that differs between variants without being declared in `varies` is a confound, unless a person
        vouched for it; a declared variable that no variant changes is worth knowing too."""
        out = []
        variants = exp.live_variants()
        names = sorted({s.name for v in variants for s in v.settings if s.retracted is None and not s.per_run})
        for name in names:
            values = {v.name: exp.value_in(v, name) for v in variants}
            distinct = {_canonical(x) for x in values.values()}
            if len(distinct) > 1 and name not in exp.varies:
                vouch = self.accepted_difference(exp, name, values)
                listing = " vs ".join(f"{show(x)} ({k})" for k, x in values.items())
                if vouch is None:
                    out.append(Caveat(code="confound", subject=f"{exp.id}/{name}", blocks=True,
                                      text=f"confound: {name} {listing}; {exp.id} varies only {', '.join(exp.varies) or 'nothing declared'}"))
                else:
                    out.append(Caveat(code="confound_accepted", subject=f"{exp.id}/{name}", blocks=False, text=f"{name} differs between variants ({listing}); accepted by {vouch.by} ({vouch.id})"))
        for name in exp.varies:
            values = {_canonical(exp.value_in(v, name)) for v in variants}
            if len(variants) > 1 and len(values) == 1:
                out.append(Caveat(code="varies_unchanged", subject=f"{exp.id}/{name}", blocks=False,
                                  text=f"{exp.id} declares it varies {name}, but every variant has the same value"))
        return out

    def verdict(self, claim: Claim, *, _cache: Optional[dict] = None) -> ClaimVerdict:
        base = {"claim": claim.id, "statement": claim.statement, "criterion": claim.criterion(), "origin": claim.origin}
        decisions = self.decisions_on(claim.id)
        latest = f"{decisions[-1].outcome} by {decisions[-1].by} ({decisions[-1].id})" if decisions else None
        caveats: list[Caveat] = []
        if claim.retracted is not None:
            return ClaimVerdict(**base, status="untested", caveats=[Caveat(code="retracted", subject=claim.id, blocks=True, text=f"retracted: {claim.retracted.why}")], decision=latest)
        tamper = self.tampered()
        if tamper:
            caveats.append(Caveat(code="edited_outside_rb", subject=claim.id, blocks=True,
                                  text=f"{len(tamper)} object(s) in .rb/ changed outside rb, first {tamper[0]['why']}: rb doctor lists them"))
        if claim.source is not None:
            if claim.source.resolved is None:
                caveats.append(Caveat(code="cited_unchecked", subject=claim.id, blocks=True,
                                      text=f"the cited {claim.target:g} rests on {claim.source.label()}, which rb did not check"))
            else:
                state, why = recheck(claim.source, claim.target, self.root, claim.metric.split(".")[-1], named=False)
                if state in ("stale", "conflict"):
                    caveats.append(Caveat(code="cited_source_changed", subject=claim.id, blocks=True, text=f"the cited number's source: {why}"))
        if claim.experiment is None:
            caveats.append(Caveat(code="no_experiment", subject=claim.id, blocks=True, text="not linked to an experiment, so no evidence can test it"))
            return ClaimVerdict(**base, status="inherited" if claim.origin == "inherited" else "untested", caveats=caveats,
                                not_established_because=_codes(caveats), decision=latest)
        exp = self.load("experiment", claim.experiment)
        inv = self.investigation
        if exp.retracted is not None:
            caveats.append(Caveat(code="experiment_retracted", subject=exp.id, blocks=True, text=f"{exp.id} was retracted: {exp.retracted.why}"))
        drift = self.spec_drift(exp)
        if drift:
            caveats.append(Caveat(code="drift", subject=exp.id, blocks=True, text=drift))
        caveats += self.setting_caveats(exp, inv.parent is not None)
        fixed_at = self.criterion_fixed_at(claim, exp)
        if fixed_at is None:
            how = (f"a person freezes {exp.id}, then attach a new run" if exp.frozen is None
                   else f"it was written after {exp.id} was frozen; a person adopts it with rb freeze {exp.id} --amend --why \"...\", then attach a new run")
            caveats.append(Caveat(code="criterion_not_fixed_by_person", subject=claim.id, blocks=True, text=f"criterion written by {claim.created_by}; {how}"))
        cited_differs = [c for c in caveats if c.code == "cited_differs"]
        aliases = self.metric_aliases()
        spec_now = self.spec_sha(exp)
        snapshot_now = self.settings_snapshot(exp)
        evid = [e for e in self.all("evidence") if e.experiment == exp.id]
        observations: list[Observation] = []
        first_of: dict[str, str] = {}
        counts_from = fixed_at or claim.created_at
        fixed_by_freeze = exp.frozen is not None and fixed_at == exp.frozen.at and fixed_at != claim.created_at
        for e in evid:
            if e.retracted is not None:
                continue
            value, problem = self.read_metric(exp, claim.metric, e, aliases)
            if problem:
                caveats.append(Caveat(code="not_read", subject=e.id, blocks=False, text=f"{e.id} not read: {problem}"))
                continue
            if value is None:
                continue
            role, reason = "confirmatory", ""
            mism = (e.receipt.config.mismatches if e.receipt.config else [])
            same_numbers = "numbers:" + _canonical(e.metrics)
            if e.synthetic:
                role, reason = "not_counted", "synthetic: rb's example data or its synthetic adapter, not evidence about any model"
            elif self.edited_outside("evidence", e.id):
                role, reason = "not_counted", "edited outside rb"
            elif e.spec_sha256 != spec_now:
                role, reason = "not_counted", "spec changed since: " + (_diff(e.settings, snapshot_now) or "the variants or declared variables changed")
            elif mism:
                role, reason = "not_counted", "ran with " + "; ".join(f"{m['name']} {show(m['ran'])} (the spec says {show(m['spec'])})" for m in mism[:3])
            elif same_numbers in first_of:
                role, reason = "not_counted", f"the same numbers as {first_of[same_numbers]}: a repeat adds nothing, and counts once"
            elif e.created_at < claim.created_at:
                role, reason = "exploratory", f"attached before {claim.id} was written"
            elif e.created_at < counts_from or (fixed_by_freeze and e.id in exp.frozen.after_evidence):
                role, reason = "exploratory", f"attached before a person fixed {claim.id}'s criterion ({exp.id} frozen {exp.frozen.at[:16] if exp.frozen else ''})"
            elif e.per_run and _run_key(e) in first_of:
                role, reason = "not_counted", f"repeats {', '.join(f'{k}={show(x)}' for k, x in sorted(e.per_run.items()))} of {first_of[_run_key(e)]}: not an independent run"
            if role != "not_counted":
                first_of.setdefault(same_numbers, e.id)
            if role == "confirmatory" and e.per_run:
                first_of.setdefault(_run_key(e), e.id)
            holds, margin = _judge(claim, value)
            observations.append(Observation(evidence=e.id, value=value, holds=holds, margin=margin, role=role, reason=reason, basis=e.basis, per_run=e.per_run))
        conf = [o for o in observations if o.role == "confirmatory"]
        n = len(conf)
        values = [o.value for o in conf]
        mean = statistics.fmean(values) if values else None
        sd = statistics.stdev(values) if len(values) >= 2 else None
        holding = sum(1 for o in conf if o.holds)
        if not conf:
            status = "inherited" if claim.origin == "inherited" else "untested"
            exploring = [o for o in observations if o.role == "exploratory"]
            if exploring:
                meets = [o.evidence for o in exploring if o.holds]
                caveats.append(Caveat(code="exploratory_only", subject=claim.id, blocks=True,
                                      text=f"{len(exploring)} exploratory run(s); {len(meets)} meet it ({', '.join(meets) or 'none'}). Attach a run after the claim" + (" and the freeze" if exp.frozen else "")))
        elif claim.over == "mean":
            holds, _ = _judge(claim, mean)  # type: ignore[arg-type]
            status = HOLDS[claim.origin] if holds else FAILS[claim.origin]
        elif holding == n:
            status = HOLDS[claim.origin]
        elif holding == 0:
            status = FAILS[claim.origin]
        else:
            status = "mixed"
        if conf and claim.origin == "cited" and cited_differs:
            status = "not_comparable"
            caveats.append(Caveat(code="not_comparable", subject=claim.id, blocks=True, text="; ".join(c.text for c in cited_differs)))
        for o in observations:
            if o.role != "confirmatory":
                caveats.append(Caveat(code=o.role, subject=o.evidence, blocks=False, text=f"{o.evidence} {o.role.replace('_', ' ')}: {o.reason}"))
        if conf:
            if n < claim.min_n:
                caveats.append(Caveat(code="too_few_runs", subject=claim.id, blocks=True, text=f"{n} confirmatory run(s); the claim needs {claim.min_n}"))
            elif n == 1:
                caveats.append(Caveat(code="single_run", subject=claim.id, blocks=False, text="1 run (no repeat)"))
            spread = 2 * sd if sd is not None and n >= 3 else None     # what the runs themselves show; a stated noise never lowers it
            noise = max((x for x in (claim.noise, spread) if x is not None), default=None)
            judged = [_judge(claim, mean)[1]] if claim.over == "mean" else [o.margin for o in conf]  # type: ignore[arg-type]
            if noise is None:
                caveats.append(Caveat(code="noise_unknown", subject=claim.id, blocks=False,
                                      text="run-to-run noise unknown: give the claim --noise, or attach 3 or more runs" if claim.target != 0 or claim.comparator == "equals"
                                      else "any margin holds against a target of 0; give the claim --noise"))
            elif any(abs(m) < noise for m in judged):
                caveats.append(Caveat(code="borderline", subject=claim.id, blocks=True, text=f"within the noise ({noise:g}) of the criterion: another run may land on the other side"))
            if all(e.receipt.config is None or not (e.receipt.config.matches or e.receipt.config.mismatches) for e in evid if e.id in {o.evidence for o in conf}):
                caveats.append(Caveat(code="config_unchecked", subject=claim.id, blocks=False, text="no run config was compared with the spec (attach with --config)"))
            typed = [o.evidence for o in conf if o.basis == "typed"]
            if typed:
                caveats.append(Caveat(code="typed", subject=claim.id, blocks=False, text=f"typed in, not read from a file or run: {', '.join(typed)}"))
        caveats = _unique(caveats)
        blocking = [c for c in caveats if c.blocks]
        established = status in SETTLED and not blocking
        return ClaimVerdict(**base, status=status, established=established, not_established_because=[] if established else _codes(blocking),
                            n=n, holding=holding, mean=mean, sd=sd, observations=observations, caveats=caveats, decision=latest)

    # ------------------------------------------------------------ views over the whole state

    def _attach_hint(self, c: Claim) -> str:
        """The attach command that would test a claim: the variants' own numbers (rb works out change.*, never the
        caller), and every per-run value the experiment declares."""
        exp = self.load("experiment", c.experiment) if c.experiment else None
        if exp is None:
            return f"rb evidence attach <experiment> {c.metric}=<value>"
        head, _, rest = c.metric.partition(".")
        base = exp.by_role("baseline")
        if head == "change" and rest:
            v2, _, m2 = rest.partition(".")
            cand = exp.variant(v2) if m2 and exp.variant(v2) else None
            m = m2 if cand else rest
            cands = [cand] if cand else exp.by_role("candidate")[:1]
            pairs = " ".join(f"{v.name}.{m}=<value>" for v in [*base[:1], *cands])
        else:
            pairs = f"{c.metric}=<value>"
        per_run = " ".join(f"--set {n}=<value>" for n, s in exp.all_settings() if s.per_run and s.retracted is None)
        return f"rb evidence attach {exp.id} {pairs}" + (f" {per_run}" if per_run else "") + ' --command "..."'

    def counted_evidence(self, exp: Experiment) -> list[Evidence]:
        """The evidence that counts for the experiment as a whole, whatever any claim says: not synthetic, not edited,
        under the current spec, run with a config that agrees with it, and each run once."""
        spec_now = self.spec_sha(exp)
        seen: set[str] = set()
        out = []
        for e in sorted(self.live("evidence"), key=lambda x: x.created_at):
            if e.experiment != exp.id or e.synthetic or e.spec_sha256 != spec_now or self.edited_outside("evidence", e.id):
                continue
            if e.receipt.config is not None and e.receipt.config.mismatches:
                continue
            keys = ["numbers:" + _canonical(e.metrics)] + ([_run_key(e)] if e.per_run else [])
            if any(k in seen for k in keys):
                continue
            seen.update(keys)
            out.append(e)
        return out

    def compare(self, experiment_id: str) -> dict:
        """The experiment's own table: its variants as rows, the settings it varies and the metrics its evidence reports
        as columns, each metric with its mean over counted evidence and its change against the baseline, read in the
        direction the catalogue gives that metric."""
        exp = self.load("experiment", experiment_id)
        aliases = self.metric_aliases()
        catalogue = {m.id: m for m in self.live("metric")}
        evid = self.counted_evidence(exp)
        variants = sorted(exp.live_variants(), key=lambda v: (ROLES.index(v.role), v.name))
        per: dict[str, dict[str, list[float]]] = {v.name: {} for v in variants}
        overall: dict[str, list[float]] = {}
        for e in evid:
            for k, x in e.metrics.items():
                canon = _canon(k, exp, aliases)
                head, _, rest = canon.partition(".")
                if head in per and rest:
                    per[head].setdefault(rest, []).append(x)
                else:
                    overall.setdefault(canon, []).append(x)
        metric_names = sorted({m for d in per.values() for m in d}, key=lambda m: (m not in catalogue, m))
        base = next((v for v in variants if v.role == "baseline"), None)
        rows = []
        for v in variants:
            cells = {}
            for m in metric_names:
                xs = per[v.name].get(m, [])
                if not xs:
                    continue
                mean = statistics.fmean(xs)
                cell: dict[str, Any] = {"mean": mean, "n": len(xs)}
                if base is not None and v is not base and per[base.name].get(m):
                    delta = _minus(mean, statistics.fmean(per[base.name][m]))
                    direction = catalogue[m].direction if m in catalogue else "none"
                    cell["delta"] = delta
                    cell["better"] = None if direction == "none" or delta == 0 else (delta < 0) == (direction == "minimize")
                cells[m] = cell
            rows.append({"variant": v.name, "role": v.role, "varies": {n: exp.value_in(v, n) for n in exp.varies}, "metrics": cells})
        return {"experiment_id": exp.id, "title": exp.title, "varies": exp.varies, "baseline": base.name if base else None,
                "metrics": [{"name": m, "unit": catalogue[m].unit if m in catalogue else "", "direction": catalogue[m].direction if m in catalogue else "none",
                             "declared": m in catalogue} for m in metric_names],
                "rows": rows, "overall": {k: {"mean": statistics.fmean(v), "n": len(v)} for k, v in sorted(overall.items())},
                "evidence_count": len(evid)}


    def status(self, experiment: Optional[str] = None) -> dict:
        """The whole state, read the way a person starts their day: a tally, then what needs a person, then what an agent
        can do, each item with who can act, a code, and the exact command."""
        inv = self.investigation
        who = self.actor()
        experiments = self.live("experiment")
        if experiment is not None:
            self.load("experiment", experiment)
            experiments = [e for e in experiments if e.id == experiment]
        exp_ids = {e.id for e in experiments}
        claims = [c for c in self.live("claim") if experiment is None or c.experiment in exp_ids]
        evidence = [e for e in self.live("evidence") if e.experiment in exp_ids]
        questions = self.live("question") if experiment is None else []
        hypotheses = self.live("hypothesis") if experiment is None else [h for h in self.live("hypothesis") if any(h.id in e.hypotheses for e in experiments)]
        assumptions = [a for a in self.live("assumption") if experiment is None or set(a.applies_to) & exp_ids]
        decisions = self.live("decision")
        verdicts = {c.id: self.verdict(c) for c in claims}
        items: list[dict] = []

        def item(who_: str, code: str, subject: str, what: str, do: str) -> None:
            items.append({"who": who_, "code": code, "subject": subject, "what": what, "do": do})

        tally: dict[str, int] = {}
        for v in verdicts.values():
            key = "established" if v.established else (f"{v.status}, not established" if v.status in SETTLED else v.status)
            tally[key] = tally.get(key, 0) + 1
        gate = {"unestablished": False, "untested": False, "refuted": False, "not_reproduced": False, "undecided": False, "unknown": False, "stale": False}
        claimed_metrics: dict[str, set[str]] = {}
        for c in claims:
            v = verdicts[c.id]
            gate["unestablished"] |= not v.established
            gate["untested"] |= v.status == "untested"
            gate["refuted"] |= v.status == "refuted"
            gate["not_reproduced"] |= v.status == "not_reproduced"
            claimed_metrics.setdefault(c.experiment or "", set()).add(c.metric)
            last = next(iter(reversed(self.decisions_on(c.id))), None)
            if last is None and v.status in UNSETTLED:
                gate["undecided"] = True
                item("person", "undecided", c.id, f"{c.id} is {v.status.replace('_', ' ')} and nobody has decided on it: {c.statement}",
                     f'rb decide {c.id} accept|reject|investigate --why "..."')
            elif last is not None and last.verdict is not None and last.verdict != v.status:
                gate["undecided"] = True       # a decision is read against what it was made on: that has changed
                item("person", "decision_outdated", c.id, f"{last.id} ({last.outcome}) was made on {last.verdict}; {c.id} is now {v.status}",
                     f'rb decide {c.id} accept|reject|investigate --why "..."')
            if last is not None and last.outcome == "investigate":
                item("person", "investigating", c.id, f"{c.id} · investigating since {last.id} ({last.by}): {last.why}",
                     f'rb decide {c.id} accept|reject --why "..." when it is resolved')
            for cav in v.caveats:
                if cav.code == "criterion_not_fixed_by_person" and c.experiment:
                    frozen = self.load("experiment", c.experiment).frozen is not None
                    item("person", cav.code, c.id, f"{c.id}: {cav.text}", f'rb freeze {c.experiment}{" --amend" if frozen else ""} --why "..."')
                elif cav.code == "untested" or (v.status == "untested" and cav.code == "exploratory_only"):
                    item("agent", "exploratory_only", c.id, f"{c.id}: {cav.text}", self._attach_hint(c))
                elif cav.code == "too_few_runs":
                    item("agent", cav.code, c.id, f"{c.id}: {cav.text}", self._attach_hint(c))
                elif cav.code == "borderline":
                    item("agent", cav.code, c.id, f"{c.id}: {cav.text}", self._attach_hint(c) + "   (more runs narrow it)")
                elif cav.code == "cited_source_changed":
                    gate["stale"] = True
                    item("person", cav.code, c.id, f"{c.id}: {cav.text}", f'git diff -- {c.source.path if c.source else ""}   (restore the cited text, or retract {c.id} and write it again against the source as it is: rb retract {c.id} --why "...")')
                elif cav.code == "cited_unchecked":
                    item("agent", cav.code, c.id, f"{c.id}: {cav.text}", "save the source in the repository; a cited claim is checked when written, so write it again against the file")
            if v.status == "untested" and not any(x.code == "exploratory_only" for x in v.caveats) and c.experiment:
                item("agent", "untested", c.id, f"{c.id} is untested: {c.statement}", self._attach_hint(c))
            if not c.experiment:
                item("agent", "no_experiment", c.id, f"{c.id} is on no experiment, so no evidence can test it: {c.statement}",
                     f'rb claim add "{c.statement}" -e <experiment> --metric {c.metric} ...   (then rb retract {c.id} --why "moved onto an experiment")')
        tamper = self.tampered()
        if tamper:
            gate = {k: True for k in gate}          # a state changed outside rb fails every gate until it is restored or adopted
            item("anyone", "edited_outside_rb", ", ".join(t["id"] for t in tamper[:6]) + (" ..." if len(tamper) > 6 else ""),
                 f"{len(tamper)} object(s) in .rb/ changed outside rb, first {tamper[0]['why']}",
                 'rb doctor --restore   (puts back what rb last wrote; if the change is right, a person runs rb doctor --adopt --why "..." instead)')
        for e in experiments:
            drift = self.spec_drift(e)
            if drift:
                gate["stale"] = True
                item("person", "drift", e.id, drift, f'rb freeze {e.id} --amend --why "..."   (adopts the spec as it is now)')
            amend = ' --amend --why "..."' if e.frozen else ""
            owner = "person" if e.frozen else "agent"
            for cav in self.setting_caveats(e, inv.parent is not None):
                name = cav.subject.split("/", 1)[1]
                s_ = e.lookup(name)
                label = s_.source.label() if s_ is not None and s_.source is not None else None
                if cav.code == "unknown":
                    gate["unknown"] = True
                    item(owner, "unknown", cav.subject, f"{cav.subject} is unknown and required", f"rb spec set {e.id} {name} <value> --source <file#key>{amend}")
                elif cav.code == "unknown_optional":
                    item(owner, "unknown_optional", cav.subject, f"{cav.subject} is unknown (optional)", f"rb spec set {e.id} {name} <value> --source <file#key>{amend}")
                elif cav.code == "provisional":
                    if s_ is not None and s_.source is not None and s_.source.checkable():
                        item("agent", "provisional", cav.subject, f"{cav.subject}: {cav.text}", f"rb spec verify {e.id} {name}")
                    else:
                        value = shlex.quote(show(s_.value)) if s_ is not None and s_.value is not None else "<value>"
                        item(owner, "provisional", cav.subject, f"{cav.subject}: {cav.text}",
                             f"rb spec set {e.id} {name} {value} --source <file#key that states it>{amend}   (or a person: rb decide {e.id}/{name} accept --why \"...\")")
                elif cav.code in ("stale", "conflict"):
                    gate["stale"] = True
                    where = f" --source {shlex.quote(label)}" if label else ""
                    fix = (f"rb spec set {e.id} {name} <the value it states now>{where}{amend}   (or point --source at where the value is stated)" if cav.code == "stale"
                           else f"rb spec set {e.id} {name} <the value the source states>{where}{amend}")
                    item(owner, cav.code, cav.subject, f"{cav.subject}: {cav.text}", fix)
                elif cav.code == "source_changed":
                    item("agent", cav.code, cav.subject, f"{cav.subject}: {cav.text}", f"rb spec verify {e.id} {name}")
                elif cav.code == "confound":
                    fix = (f'rb spec vary {e.id} {name} --amend --why "..."   (it differs on purpose) or rb decide {cav.subject} accept --why "..."   (it does not matter)'
                           if e.frozen else f'rb spec vary {e.id} {name}   (it differs on purpose), or set it the same in every variant')
                    item(owner, cav.code, cav.subject, cav.text, fix)
                elif cav.code in ("inherited_without_parent", "rejected_setting"):
                    item(owner, cav.code, cav.subject, cav.text, f"rb spec set {e.id} {name} <value> --source <file#key>{amend}")
            unread = self.unread_metrics(e, [ev for ev in evidence if ev.experiment == e.id], [c for c in claims if c.experiment == e.id])
            if unread and len(unread) <= 12:
                item(owner, "unclaimed_metric", e.id, f"{e.id}: evidence reports {', '.join(unread[:6])}{' ...' if len(unread) > 6 else ''}, which no claim reads (write the claim before the next run)",
                     f'rb claim add "<what it should show>" -e {e.id} --metric {unread[0]} --at-most <x>{amend}')
        for q in questions:
            if q.status == "open":
                item("agent", "open_question", q.id, f"{q.id} is open: {q.text}", f'rb hypothesis add "..." --question {q.id}')
        tested = {h for e in experiments for h in e.hypotheses} | {c.hypothesis for c in claims if c.hypothesis}
        for h in hypotheses:
            if h.status in ("proposed", "active") and h.id not in tested:
                item("agent", "untested_hypothesis", h.id, f"nothing tests {h.id}: {h.statement}", f'rb experiment add "..." --hypothesis {h.id}')
        for a in assumptions:
            if a.status == "open":
                item("person", "unchecked_assumption", a.id, f"{a.id} is not checked: {a.text}", f'rb decide {a.id} accept|reject --why "..."')
        merged: dict[tuple[str, str], dict] = {}      # one command that clears several items is listed once
        for it in items:
            key = (it["who"], it["do"])
            if key in merged and it["subject"] not in merged[key]["subject"].split(", "):
                first = merged[key]
                first["subject"] += f", {it['subject']}"
                first["what"] = f"{first['subject']}: " + first["what"].split(": ", 1)[-1]
            else:
                merged.setdefault(key, dict(it))
        items = sorted(merged.values(), key=lambda i: {"person": 0, "agent": 1, "anyone": 2}[i["who"]])
        exp_rows = []
        for e in experiments:
            counts = {k: 0 for k in ("verified", "provisional", "unknown", "inherited", "per_run")}
            for _, s_ in e.all_settings():
                counts["per_run" if s_.per_run else s_.status] += 1
            ev = [x for x in self.all("evidence") if x.experiment == e.id]
            exp_rows.append({"id": e.id, "title": e.title, "hypotheses": e.hypotheses, "varies": e.varies,
                             "variants": [{"name": v.name, "role": v.role} for v in e.live_variants()],
                             "frozen": e.frozen.model_dump() if e.frozen else None, "amendment_count": len(e.amendments),
                             "spec_drift": self.spec_drift(e), "settings_count": counts,
                             "blocking": [c.subject.split("/", 1)[1] for c in self.setting_caveats(e, inv.parent is not None) if c.blocks and c.code in ("unknown",)],
                             "evidence_count": len([x for x in ev if x.retracted is None]), "retracted_count": len([x for x in ev if x.retracted is not None])})
        return {
            "investigation": inv.model_dump(mode="json"), "root": str(self.root), "actor": {"id": who.id, "via": who.via},
            "experiment_id": experiment,
            "counts": {"question_count": len(questions), "hypothesis_count": len(hypotheses), "assumption_count": len(assumptions),
                       "experiment_count": len(experiments), "claim_count": len(claims), "evidence_count": len(evidence), "decision_count": len(decisions)},
            "tally": tally,
            "claims": [{**verdicts[c.id].model_dump(mode="json"), "experiment_id": c.experiment, "hypothesis_id": c.hypothesis} for c in claims],
            "experiments": exp_rows,
            "evidence": [{"id": x.id, "experiment_id": x.experiment, "basis": x.basis, "metrics": sorted(x.metrics), "per_run": x.per_run,
                          "synthetic": x.synthetic, "attached": x.created_at, "by": x.created_by} for x in evidence],
            "questions": [q.model_dump(mode="json") for q in questions],
            "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
            "assumptions": [a.model_dump(mode="json") for a in assumptions],
            "decisions": [d.model_dump(mode="json") for d in decisions],
            "metrics": [m.model_dump(mode="json") for m in self.live("metric")],
            "open": items,
            "gate": gate,
        }


# ---------------------------------------------------------------- reading rb runs as evidence


def metric_key(metric_id: str) -> str:
    """A run's metric id as an evidence metric name (letters, digits and _.:/-)."""
    return re.sub(r"[^A-Za-z0-9_.:/-]+", "_", metric_id).strip("_")[:60] or "metric"


def evidence_from_run(bundle: Any, run_dir: Optional[Path], root: Path, given: Optional[Path] = None) -> dict:
    """What an `rb review run` / `rb review import` contributes as evidence: its summary numbers (`baseline.<metric>`,
    `candidate.<metric>`, `change.<metric>`, `with_gt`, `regressions`, `flagged`, ...) and the fields of its own receipt.
    A run with no labelled case reports no error numbers: an error that was not measured is not a zero."""
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
    record: dict[str, Any] = {"run_id": bundle.run_id, "source": bundle.source, "metric": bundle.metric.model_dump(), "review_verdict": findings.verdict.line,
                              "models": {"baseline": bundle.baseline.name, "candidate": bundle.candidate.name}}
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
            record.update({k: rec.get(k) for k in ("command", "checkpoints", "dataset", "model_code", "seeds", "adapter_agreement", "environment", "adapter") if rec.get(k) is not None})
            adapter_id = adapter_id or (rec.get("adapter") or {}).get("id")
        except ValueError:
            pass
    path, file = None, None
    where = run_dir if run_dir is not None else given
    if where is not None:
        rel = os.path.relpath(os.path.abspath(where), root)
        path = rel if not rel.startswith("..") else os.path.abspath(where)
        if run_dir is None:
            file = path
    return {"metrics": metrics, "record": record, "synthetic": bundle.source == "example" or adapter_id == "synthetic", "path": path, "file": file,
            "names": {"baseline": bundle.baseline.name, "candidate": bundle.candidate.name}}


# ---------------------------------------------------------------- helpers


def _canon(name: str, exp: Experiment, aliases: dict[str, str]) -> str:
    """A metric name with its role prefix turned into the variant's name and its base name through the catalogue's
    aliases: candidate.endpoint_error -> int8.epe."""
    head, sep, rest = name.partition(".")
    if sep and head in ("baseline", "candidate"):
        vs = exp.by_role(head)
        if len(vs) == 1:
            head = vs[0].name
    if sep and (head == "change" or exp.variant(head) is not None or head in ROLES):
        inner_head, isep, inner_rest = rest.partition(".")
        if head == "change" and isep and exp.variant(inner_head) is not None:
            return f"change.{inner_head}.{aliases.get(inner_rest, inner_rest)}"
        return f"{head}.{aliases.get(rest, rest)}"
    return aliases.get(name, name)


def _variants_of(e: Evidence) -> set[str]:
    return {k.split(".")[0] for k in e.metrics if "." in k}


def _config_value(flat: dict[str, Any], name: str) -> tuple[Any, bool]:
    if name in flat:
        return flat[name], True
    leaf = name.split(".")[-1]
    hits = [k for k in flat if k.split(".")[-1] == leaf]
    if len(hits) == 1:
        return flat[hits[0]], True
    return None, False


def _produced_from(run_record: Optional[dict]) -> dict:
    code = (run_record or {}).get("model_code") or {}
    sha = code.get("sha") if isinstance(code, dict) else None
    return {"commit": sha, "from": "record.json" if sha else "unknown"}


def _diff(then: dict[str, Any], now_: dict[str, Any]) -> str:
    changed = [f"{k} {show(then.get(k))} -> {show(now_.get(k))}" for k in sorted(set(then) | set(now_)) if _canonical(then.get(k)) != _canonical(now_.get(k))]
    return ", ".join(changed[:4]) + (f" and {len(changed) - 4} more" if len(changed) > 4 else "")


def _clamp(x: float) -> float:
    return x if math.isfinite(x) else math.copysign(1.7976931348623157e308, x)


def _session(via: Optional[str], agent: Optional[str]) -> tuple[str, Optional[str]]:
    ctx_via, ctx_agent = _SESSION.get()
    return via or ctx_via, agent or ctx_agent


def _minus(a: float, b: float) -> float:
    """a - b without the float noise of the subtraction itself: 5.64 - 5.62 is 0.02, not 0.019999999999999574. Rounded to
    12 significant digits of the larger operand, far below any precision an evaluation reports."""
    scale = max(abs(a), abs(b))
    if scale == 0 or not math.isfinite(scale):
        return a - b
    return round(a - b, 11 - math.floor(math.log10(scale)))


def _judge(claim: Claim, value: float) -> tuple[bool, float]:
    """(holds, margin): how far inside (positive) or outside (negative) the criterion the value is."""
    eps = 1e-12 * max(1.0, abs(claim.target))
    if claim.comparator == "equals":
        margin = _clamp(float(claim.tolerance or 0.0) - abs(value - claim.target))
    else:
        margin = _clamp(claim.target - value if claim.comparator == "at_most" else value - claim.target)
    return margin >= -eps, margin


def _build(model: type[BaseModel], **fields: Any) -> Any:
    try:
        return model(**fields)
    except ValidationError as exc:
        raise _invalid(exc)


def _invalid(exc: ValidationError) -> RBError:
    problems = [f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()]
    return RBError("E_OBJECT_INVALID", message=problems[0] if len(problems) == 1 else "The values given are not valid.", problems=problems if len(problems) > 1 else [])


def _same_value(a: Any, b: Any) -> bool:
    """Exactly the same value: same JSON type and text. 1 is not 1.0, and 1700000001 is not 1700000002."""
    return _canonical(a) == _canonical(b) and type(a) is type(b)


def _same_source(a: Optional[Source], b: Optional[Source]) -> bool:
    if a is None or b is None:
        return a is b
    return a.model_dump(exclude={"resolved"}) == b.model_dump(exclude={"resolved"})


def _describe_change(name: str, old: Optional[Setting], new: Setting) -> str:
    if old is None:
        extra = " (per-run)" if new.per_run else ("" if new.required else " (optional)")
        return f"setting {name}: new, {show(new.value)}{extra}"
    parts = []
    if not _same_value(old.value, new.value) or (old.status != new.status and "unknown" in (old.status, new.status)):
        parts.append(f"{show(old.value)} -> {show(new.value)}" + (f" (was {type(old.value).__name__})" if old.value is not None and new.value is not None and type(old.value) is not type(new.value) else ""))
    if old.required != new.required:
        parts.append("required -> optional" if old.required else "optional -> required")
    if old.per_run != new.per_run:
        parts.append("now per-run" if new.per_run else "no longer per-run")
    if not _same_source(old.source, new.source):
        parts.append(f"source {old.source.label() if old.source else 'none'} -> {new.source.label() if new.source else 'none'}")
    if old.note != new.note:
        parts.append("note changed")
    return f"setting {name}: " + ("; ".join(parts) if parts else "unchanged")


def _codes(caveats: list[Caveat]) -> list[str]:
    out: list[str] = []
    for c in caveats:
        if c.blocks and c.code not in out:
            out.append(c.code)
    return out


def _unique(caveats: list[Caveat]) -> list[Caveat]:
    seen, out = set(), []
    for c in caveats:
        key = (c.code, c.subject, c.text)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
