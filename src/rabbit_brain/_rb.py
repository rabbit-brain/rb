"""Calling rb from its Python package. The SDK and the terminal UI hold no rules of their own: every read and every
write is asked of rb, through `rb bridge` (one JSON request, one JSON answer), so the Python surface and the command
line can never disagree about what the record says.

Which rb: RB_BIN when set, else the `rb` installed with this package (the scripts directory of this Python).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import warnings
from dataclasses import dataclass
from typing import Any, Optional

from . import __version__
from .errors import EXIT_ENVIRONMENT, RBError, RBWarning

UNSET: Any = object()
# pinned: unpinned, pip installs the newest final release, which can be another version than this one
REINSTALL = f'pip install --force-reinstall "rabbit-brain=={__version__}"'


def _quiet() -> dict:
    """Windows: a Python with no console window (an editor's, a notebook's) starts rb without one too, so no window
    flashes up for each question. In a terminal rb shares the terminal, as everywhere else."""
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow():
            return {}
    except Exception:  # noqa: BLE001 - no way to ask: start rb without a window, which is right in either case
        pass
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


QUIET = _quiet()


def binary() -> Optional[str]:
    """The rb binary this package calls, or None when there is none to find."""
    given = os.environ.get("RB_BIN")
    if given:
        return given
    exe = "rb" + (sysconfig.get_config_var("EXE") or "")
    places = []
    try:
        places.append(sysconfig.get_path("scripts"))
        places.append(sysconfig.get_path("scripts", scheme=sysconfig.get_preferred_scheme("user")))
    except Exception:  # noqa: BLE001 - an unusual Python layout only means fewer places to look
        pass
    places.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))   # pip install --target
    for d in places:
        if d and os.path.isfile(os.path.join(d, exe)):
            return os.path.join(d, exe)
    return None


def found() -> str:
    """The rb this package calls. When there is none, that is said once, with what to do."""
    rb = binary()
    if rb is None:
        raise RBError("E_INTERNAL", message="This Python package found no rb beside it: the SDK and the screen ask rb for everything.",
                      fix=f"Install them together: {REINSTALL}. Or set RB_BIN to the rb binary.",
                      exit_code=EXIT_ENVIRONMENT)
    return rb


def _jsonable(v: Any) -> Any:
    """What json does not take as it is: a path, a numpy or torch scalar, a set."""
    if isinstance(v, os.PathLike):
        return os.fspath(v)
    if hasattr(v, "item") and callable(v.item):
        try:
            return v.item()
        except Exception:  # noqa: BLE001 - an array is not a scalar: said below
            pass
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    raise RBError("E_OBJECT_INVALID", message=f"rb records text, numbers, true and false, lists and mappings (got {type(v).__name__}).")


def command() -> list:
    """The command line that runs rb, for what the screen runs as the user would type it."""
    return [found()]


def environment() -> dict:
    """The environment rb runs in: this one, plus what only this process knows about the Python doing the work."""
    env = dict(os.environ)
    if sys.executable:
        env["RB_PYTHON"] = sys.executable          # a run's receipt fingerprints the Python that made it
    torch = sys.modules.get("torch")
    cuda = getattr(getattr(torch, "version", None), "cuda", None) if torch is not None else None
    if cuda:
        env["RB_CUDA"] = str(cuda)                 # CUDA exists only on an imported torch: said by the process that has it
    else:
        env.pop("RB_CUDA", None)
    return env


def _answer(line: str) -> dict:
    rb = found()
    try:
        r = subprocess.run([rb, "bridge"], input=(line + "\n").encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=environment(), **QUIET)
    except OSError as exc:
        raise RBError("E_INTERNAL", message=f"rb could not be started ({rb}): {exc}", exit_code=EXIT_ENVIRONMENT)
    out = r.stdout.decode("utf-8", "replace").strip()
    try:
        answer = json.loads(out.splitlines()[-1]) if out else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or "ok" not in answer:
        tail = (r.stderr.decode("utf-8", "replace").strip().splitlines() or [out[:200] or f"exit {r.returncode}"])[-1]
        raise RBError("E_INTERNAL", message=f"rb did not answer ({rb}): {tail}", exit_code=EXIT_ENVIRONMENT,
                      fix=f"This Python package and the rb it found may be different versions: {REINSTALL}.")
    return answer


@dataclass
class DryRun:
    """A write asked with dry_run=True: rb made every check the write makes and wrote nothing. `value` is what the write
    would give back; `would_write` what it would write, as `rb <command> --dry-run --json` gives it in data.would_write:
    each record file with its content (`files`), each file it would remove (`removed`), each line it would add to the
    log (`log`). Ids and times are drawn again when the write is made."""
    value: Any
    would_write: dict


def call(op: str, args: Optional[dict] = None, *, root: Any = None, via: str = "sdk", agent: Optional[str] = None,
         words: Any = UNSET, dry_run: bool = False, here: Any = None, place: Optional[str] = None, at: Optional[str] = None) -> Any:
    """Ask rb for one thing. Returns its value, or with `dry_run` a DryRun; raises RBError with what rb said when it
    refuses. `here` is the checkout the caller works in (a linked worktree nested in the record's), as op open gave it.
    `place` and `at` ask what a screen reads of another place or a past version, by the id and commit op places lists:
    read there, never written."""
    req: dict = {"op": op, "root": os.fspath(root) if root is not None else None, "via": via, "agent": agent, "args": args or {}}
    if here is not None:
        req["here"] = os.fspath(here)
    if place is not None:
        req["place"] = place
    if at is not None:
        req["at"] = at
    if words is not UNSET:
        req["words"] = words
    if dry_run:
        req["dry_run"] = True
    answer = _answer(json.dumps(req, default=_jsonable))
    if answer["ok"]:
        _said(answer.get("warnings"))
        if dry_run:
            return DryRun(answer.get("value"), answer.get("would_write") or {})
        return answer.get("value")
    e = dict(answer.get("error") or {})
    raise RBError(e.pop("code", "E_INTERNAL"), message=e.pop("message", None), fix=e.pop("fix", None),
                  exit_code=e.pop("exit_code", 2), problems=list(e.pop("problems", []) or []), extra=e)


def _said(texts: Any) -> None:
    """What rb says after a write it made (`data.warnings` on the command line), as an RBWarning at the line of code
    that asked for the write: the first one outside this package."""
    if not texts:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    frame, level = sys._getframe(1), 2
    while frame is not None and os.path.dirname(os.path.abspath(frame.f_code.co_filename)) == here:
        frame, level = frame.f_back, level + 1
    for text in texts:
        warnings.warn(str(text), RBWarning, stacklevel=level)


def check_version(answered: Any) -> None:
    """An opening answer says which rb gave it. A different version beside this package means a broken install."""
    theirs = answered.get("rb") if isinstance(answered, dict) else None
    if theirs is not None and theirs != __version__:
        raise RBError("E_INTERNAL", message=f"rb {theirs} answered, and this Python package is rabbit-brain {__version__} ({binary()}).",
                      fix=f"Reinstall so the two match: {REINSTALL}.", exit_code=EXIT_ENVIRONMENT)
