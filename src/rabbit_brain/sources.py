"""Resolving a source: the one way a knob becomes `verified`.

A file source is read (at its commit through `git show`, or from the working tree), the stated line or quote is found,
and the value has to appear in it. A run source is a JSON file and a pointer, and the value there has to equal the
knob's. Nothing here guesses: a source that cannot be read, or that does not state the value, leaves the knob
`inferred` and says why.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .investigation import Resolution, Source

NUMBER = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
BOOL = re.compile(r"\b(true|false|True|False|TRUE|FALSE)\b")


class Unresolved(Exception):
    """The source was read and does not state the value, or could not be read. `code` is the error code to report."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> Optional[str]:
    """Output of a git command in `root`, or None when git is missing, `root` is not a repository, or the command fails."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def git_bytes(root: Path, *args: str) -> Optional[bytes]:
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git_state(root: Path, exclude: str = ".rb") -> Optional[dict]:
    """The repository state a receipt records: commit, branch, and whether the tree had uncommitted changes (with a hash
    of the diff, so two runs from the same dirty tree can be told apart from two that are not). The research state's
    own directory is left out: recording evidence writes to it, and that must not make every receipt read dirty."""
    head = git(root, "rev-parse", "HEAD")
    top = git(root, "rev-parse", "--show-toplevel")
    if head is None or top is None:
        return None
    try:
        skip = (root.resolve() / exclude).relative_to(Path(top.strip()).resolve()).as_posix()
    except ValueError:
        skip = exclude
    pathspec = ["--", ":(top)", f":(top,exclude){skip}"]
    status = git(root, "status", "--porcelain", "--untracked-files=all", *pathspec) or ""
    state = {"commit": head.strip(), "branch": (git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None, "dirty": bool(status.strip())}
    if state["dirty"]:
        diff = git_bytes(root, "diff", "HEAD", *pathspec) or b""
        state["diff_sha256"] = sha256_bytes(diff)
        state["untracked"] = sum(1 for line in status.splitlines() if line.startswith("??"))
    return state


def read_file(root: Path, path: str, commit: Optional[str]) -> tuple[bytes, Optional[str]]:
    """The file's bytes and the commit they were read at (None for an uncommitted working-tree file)."""
    if commit:
        full = git(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
        if full is None:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"commit {commit} is not in the repository at {root}")
        data = git_bytes(root, "show", f"{full.strip()}:./{path}")
        if data is None:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{path} does not exist at commit {commit[:12]}")
        return data, full.strip()
    p = Path(path) if Path(path).is_absolute() else root / path
    if not p.is_file():
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{path} does not exist")
    data = p.read_bytes()
    return data, committed_as(root, path, data)


def committed_as(root: Path, path: str, data: bytes) -> Optional[str]:
    """HEAD when the working-tree file is tracked and identical to HEAD's copy; otherwise None (the file can still change
    under the knob, which the stored hash will reveal)."""
    head = git(root, "rev-parse", "HEAD")
    if head is None or Path(path).is_absolute():
        return None
    at_head = git_bytes(root, "show", f"HEAD:./{path}")
    return head.strip() if at_head is not None and at_head == data else None


def _numbers(text: str) -> list[float]:
    out = []
    for token in NUMBER.findall(text):
        try:
            out.append(float(token))
        except ValueError:
            pass
    return out


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-15)


def states(text: str, value: Any) -> bool:
    """Whether `text` states `value`. Numbers compare numerically (1e-4 is stated by "0.0001"); strings as substrings;
    booleans as a true/false token; a list when every element is stated."""
    if isinstance(value, list):
        return bool(value) and all(states(text, v) for v in value)
    if isinstance(value, bool):
        return any(t.lower() == str(value).lower() for t in BOOL.findall(text))
    if isinstance(value, (int, float)):
        return any(_close(n, float(value)) for n in _numbers(text))
    if isinstance(value, str):
        return value in text
    return False


def equal(a: Any, b: Any) -> bool:
    """A knob value against a JSON value: numbers numerically, everything else exactly, lists element by element."""
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _close(float(a), float(b))
    return a == b


def pointer_get(doc: Any, pointer: str) -> Any:
    """RFC 6901. Raises KeyError naming the step that does not exist."""
    if pointer == "":
        return doc
    cur = doc
    for raw in pointer.lstrip("/").split("/"):
        step = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            if not step.isdigit() or int(step) >= len(cur):
                raise KeyError(step)
            cur = cur[int(step)]
        elif isinstance(cur, dict):
            if step not in cur:
                raise KeyError(step)
            cur = cur[step]
        else:
            raise KeyError(step)
    return cur


def run_json_path(root: Path, path: str) -> Path:
    p = Path(path) if Path(path).is_absolute() else root / path
    return p / "record.json" if p.is_dir() else p


def render(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


def resolve(source: Source, value: Any, root: Path) -> Resolution:
    """Read the source and confirm it states `value`. Returns what was read; raises Unresolved otherwise."""
    if not source.verifiable():
        if source.kind == "file":
            raise Unresolved("E_SOURCE_UNVERIFIABLE", "a file source needs a line or a quote for rb to check it; add --source path:LINE or --quote")
        raise Unresolved("E_SOURCE_UNVERIFIABLE", f"a {source.kind} source records where the value came from but cannot be checked by reading a file; save the page as a file, or point at a file or run that states it")
    if source.kind == "run":
        p = run_json_path(root, str(source.path))
        if not p.is_file():
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path} does not exist")
        data = p.read_bytes()
        try:
            doc = json.loads(data)
        except ValueError:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{p} is not JSON")
        try:
            got = pointer_get(doc, str(source.pointer))
        except KeyError as exc:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{p} has nothing at {source.pointer} (no '{exc.args[0]}')")
        if not equal(value, got):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{p}{source.pointer} is {json.dumps(got)}, not {render(value)}")
        try:
            rel: Optional[str] = str(p.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = None
        return Resolution(at=now(), commit=committed_as(root, rel, data) if rel else None, sha256=sha256_bytes(data), text=json.dumps(got)[:400])
    data, commit = read_file(root, str(source.path), source.commit)
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    where = f"{source.path}"
    if source.line is not None:
        if source.line > len(lines):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path} has {len(lines)} lines, not {source.line}")
        found_text = lines[source.line - 1]
        found_line = source.line
        where = f"{source.path}:{source.line}"
        if source.quote and source.quote not in found_text:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} does not contain the quote; it reads: {found_text.strip()[:200]}")
    else:
        idx = text.find(str(source.quote))
        if idx < 0:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"the quote is not in {source.path}")
        found_line = text.count("\n", 0, idx) + 1
        found_text = lines[found_line - 1] if found_line <= len(lines) else str(source.quote)
        where = f"{source.path}:{found_line}"
    haystack = source.quote or found_text
    if not states(haystack, value):
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} does not state {render(value)}; it reads: {haystack.strip()[:200]}")
    return Resolution(at=now(), commit=commit, sha256=sha256_bytes(data), line=found_line, text=found_text.strip()[:400])


def still_as_resolved(source: Source, root: Path) -> Optional[str]:
    """None when a resolved source still reads as it did; otherwise why not. A source pinned to a commit cannot drift."""
    res = source.resolved
    if res is None:
        return "never resolved"
    if source.kind == "file" and source.commit:
        return None
    try:
        if source.kind == "run":
            data = run_json_path(root, str(source.path)).read_bytes()
        else:
            p = Path(str(source.path))
            data = (p if p.is_absolute() else root / p).read_bytes()
    except OSError:
        return f"{source.path} is gone"
    if sha256_bytes(data) != res.sha256:
        return f"{source.path} changed since it was verified"
    return None


def file_ref(root: Path, path: str) -> dict:
    """A file named by an evidence record: its path (relative to the directory holding .rb/), hash and size."""
    p = Path(path) if Path(path).is_absolute() else root / path
    if not p.is_file():
        raise FileNotFoundError(path)
    data = p.read_bytes()
    return {"path": path, "sha256": sha256_bytes(data), "bytes": len(data)}
