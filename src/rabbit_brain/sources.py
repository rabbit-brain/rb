"""Resolving a source: the one way a setting becomes `verified`.

A file source is read (at its commit through `git show` in the file's own repository, or from the working tree), the
stated line or quote is found, and the value has to be stated there. A run source is a JSON file and a pointer, and the
value there has to equal the setting's. Nothing here guesses, and every rule errs toward "not stated": a setting left
`provisional` by mistake costs a second look, a setting marked `verified` by mistake costs a wrong result.

What "stated" means:

- a number is a standalone token (not part of `resnet50`, `fp32`, `2024-01-05` or `1,000`), compared exactly: an integer
  only by an integer of the same value, a float by the same double (`1e-4` and `0.0001` are the same number);
- text is a whole token (`adam` is not stated by `adamw`, the text `6` not by `16`); empty text is never stated;
- a boolean by a true/false token;
- a list by a bracketed or comma-separated group with the same elements in the same order.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .investigation import Resolution, Source

NUM = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
NUMBER_TOKEN = re.compile(rf"(?<![\w.\-+]){NUM}(?![\d.]|[-+]\d|,\d)")
THOUSANDS = re.compile(r"(?<![\w.,\-+])[-+]?\d{1,3}(?:,\d{3})+(?![\w.,])")
BOOL = re.compile(r"(?<![\w.\-])(true|false|True|False|TRUE|FALSE)(?![\w.\-])")
GROUP = re.compile(r"[\[\(\{]([^\[\]\(\)\{\}]*)[\]\)\}]")
UNTRACKED_HASH_LIMIT = 16 * 1024 * 1024


class Unresolved(Exception):
    """The source was read and does not state the value, or could not be read. `code` is the error code to report."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_bytes(cwd: Path, *args: str) -> Optional[bytes]:
    """Output of a git command run in `cwd`, or None when git is missing, `cwd` is not in a repository, or it fails."""
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def git(cwd: Path, *args: str) -> Optional[str]:
    out = git_bytes(cwd, *args)
    return None if out is None else out.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- repository state for receipts


def git_state(root: Path, exclude: str = ".rb") -> Optional[dict]:
    """The repository state a receipt records: the commit (None before the first commit), the branch, whether the tree
    had uncommitted changes, and a hash covering the tracked diff and the untracked files, so two different dirty trees
    are told apart. The research state's own directory is left out: recording evidence writes to it."""
    top_s = git(root, "rev-parse", "--show-toplevel")
    if top_s is None:
        return None
    top = Path(top_s.strip())
    head = git(root, "rev-parse", "--verify", "-q", "HEAD")
    try:
        skip = (root.resolve() / exclude).relative_to(top.resolve()).as_posix()
    except ValueError:
        skip = exclude
    pathspec = ["--", ":(top)", f":(top,exclude){skip}"]
    status = git(root, "status", "--porcelain", "--untracked-files=all", *pathspec) or ""
    branch = (git(root, "symbolic-ref", "--short", "-q", "HEAD") or "").strip() or None
    state: dict[str, Any] = {"commit": head.strip() if head else None, "branch": branch, "dirty": bool(status.strip())}
    if state["dirty"]:
        h = hashlib.sha256()
        if head:
            h.update(git_bytes(root, "diff", "HEAD", *pathspec) or b"")
        # untracked files (and, before the first commit, every file) are hashed by name and content
        loose = sorted(line[3:].strip('"') for line in status.splitlines() if not head or line.startswith("??"))
        by_size = 0
        for rel in loose:
            p = top / rel
            h.update(rel.encode("utf-8") + b"\0")
            try:
                size = p.stat().st_size
                if size <= UNTRACKED_HASH_LIMIT:
                    h.update(p.read_bytes())
                else:
                    h.update(f"size={size}".encode())
                    by_size += 1
            except OSError:
                h.update(b"unreadable")
        state["diff_sha256"] = h.hexdigest()
        state["untracked"] = sum(1 for line in status.splitlines() if line.startswith("??"))
        if by_size:
            state["untracked_hashed_by_size"] = by_size   # over 16 MB: named and sized in the hash, not read
    return state


# ---------------------------------------------------------------- reading files


def full_path(root: Path, path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / p


def _repo_and_rel(file: Path) -> Optional[tuple[Path, str]]:
    """The repository holding `file`, and the file's path inside it, whatever the ledger's own root is."""
    real = Path(os.path.realpath(file))
    parent = real.parent if real.parent.exists() else file.parent
    top = git(parent, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    top_p = Path(top.strip()).resolve()
    try:
        return top_p, real.relative_to(top_p).as_posix()
    except ValueError:
        return None


def at_commit(file: Path, commit: str) -> tuple[bytes, str]:
    loc = _repo_and_rel(file)
    if loc is None:
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{file} is not in a git repository, so it cannot be read at commit {commit}")
    top, rel = loc
    full = git(top, "rev-parse", "--verify", "-q", f"{commit}^{{commit}}")
    if full is None:
        raise Unresolved("E_SOURCE_UNRESOLVED", f"commit {commit} is not in the repository at {top}")
    data = git_bytes(top, "show", f"{full.strip()}:{rel}")
    if data is None:
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{rel} does not exist at commit {commit[:12]} in {top}")
    return data, full.strip()


def committed_as(file: Path, data: bytes) -> Optional[str]:
    """HEAD of the file's own repository when the working-tree file is tracked and identical to HEAD's copy; otherwise
    None (the file can still change under the setting, which the stored hash will reveal)."""
    loc = _repo_and_rel(file)
    if loc is None:
        return None
    top, rel = loc
    head = git(top, "rev-parse", "--verify", "-q", "HEAD")
    if head is None:
        return None
    return head.strip() if git_bytes(top, "show", f"HEAD:{rel}") == data else None


def read_file(root: Path, path: str, commit: Optional[str]) -> tuple[bytes, Optional[str]]:
    """The file's bytes and the commit they were read at (None for an uncommitted working-tree file)."""
    p = full_path(root, path)
    if commit:
        return at_commit(p, commit)
    if not p.is_file():
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{path} does not exist")
    data = p.read_bytes()
    return data, committed_as(p, data)


def split_lines(text: str) -> list[str]:
    """Lines as git, sed and editors number them: split on newline only (not on form feed, a lone CR or U+2028), a
    trailing CR removed, and no empty last line for a file that ends in a newline."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


# ---------------------------------------------------------------- "stated"


def _parse_number(token: str) -> Any:
    t = token.replace(",", "")
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    return float(t)


def _num_equal(value: Any, found: Any) -> bool:
    """An integer setting equals only an integral number of the same value; a float setting equals the same double."""
    if isinstance(value, bool) or isinstance(found, bool):
        return False
    if isinstance(value, int):
        if isinstance(found, int):
            return found == value
        return isinstance(found, float) and found.is_integer() and abs(found) < 2 ** 53 and int(found) == value
    if isinstance(value, float):
        return isinstance(found, (int, float)) and float(found) == value
    return False


def _numbers(text: str) -> list[Any]:
    """Every standalone number in `text`. A number joined to another by a comma with no space ("1,000", "[3,5]") is not
    read on its own, because which reading is meant cannot be told; "1,000" is also read as one thousand."""
    out: list[Any] = []
    for m in NUMBER_TOKEN.finditer(text):
        start = m.start()
        if start >= 2 and text[start - 1] == "," and text[start - 2].isdigit():
            continue
        try:
            out.append(_parse_number(m.group(0)))
        except ValueError:
            pass
    for m in THOUSANDS.finditer(text):
        out.append(_parse_number(m.group(0)))
    return out


def _text_token(value: str, text: str) -> bool:
    if not value.strip():
        return False
    return re.search(rf"(?<![\w.\-]){re.escape(value)}(?![\w.\-])", text) is not None


def _item_is(item: str, value: Any) -> bool:
    """A list element as written (`0.9`, `"adamw"`, `true`) is exactly `value`."""
    s = item.strip().strip("'\"").strip()
    if isinstance(value, bool):
        return s.lower() == str(value).lower()
    if isinstance(value, (int, float)):
        if not re.fullmatch(NUM, s):
            return False
        return _num_equal(value, _parse_number(s))
    if isinstance(value, str):
        return bool(value.strip()) and s == value
    return False


def _list_stated(text: str, value: list) -> bool:
    candidates = [m.group(1) for m in GROUP.finditer(text)]
    candidates.append(re.split(r"[:=]", text)[-1])
    for cand in candidates:
        items = cand.split(",")
        if items and not items[-1].strip():
            items = items[:-1]          # a trailing comma
        if len(items) == len(value) and all(_item_is(i, v) for i, v in zip(items, value)):
            return True
    return False


def states(text: str, value: Any) -> bool:
    """Whether `text` states `value`, by the rules in this module's docstring."""
    if isinstance(value, list):
        return bool(value) and _list_stated(text, value)
    if isinstance(value, bool):
        return any(t.lower() == str(value).lower() for t in BOOL.findall(text))
    if isinstance(value, (int, float)):
        return any(_num_equal(value, n) for n in _numbers(text))
    if isinstance(value, str):
        return _text_token(value, text)
    return False


def equal(a: Any, b: Any) -> bool:
    """A setting value against a JSON value: numbers by the same rule as `states`, everything else exactly, lists element by
    element in order."""
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _num_equal(a, b)
    return a == b


def pointer_get(doc: Any, pointer: str) -> Any:
    """RFC 6901. Raises KeyError naming the step that does not exist."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    cur = doc
    for raw in pointer[1:].split("/"):
        step = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", step, flags=re.ASCII) or int(step) >= len(cur):
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
    p = full_path(root, path)
    return p / "record.json" if p.is_dir() else p


def render(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


# ---------------------------------------------------------------- resolving


def resolve(source: Source, value: Any, root: Path) -> Resolution:
    """Read the source and confirm it states `value`. Returns what was read; raises Unresolved otherwise, including for
    anything unexpected while reading, so one bad source never stops the others being checked."""
    try:
        return _resolve(source, value, root)
    except Unresolved:
        raise
    except (OSError, ValueError, KeyError, UnicodeError) as exc:
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.label()} could not be read: {type(exc).__name__}: {exc}")


def _resolve(source: Source, value: Any, root: Path) -> Resolution:
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
        return Resolution(at=now(), commit=committed_as(p, data), sha256=sha256_bytes(data), text=json.dumps(got)[:400])
    data, commit = read_file(root, str(source.path), source.commit)
    text = data.decode("utf-8", errors="replace")
    lines = split_lines(text)
    if source.line is not None:
        if source.line > len(lines):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path} has {len(lines)} lines, not {source.line}")
        found_text, found_line = lines[source.line - 1], source.line
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
    shown = found_text.strip()[:400] or haystack.strip()[:400]
    return Resolution(at=now(), commit=commit, sha256=sha256_bytes(data), line=found_line, text=shown)


def still_as_resolved(source: Source, value: Any, root: Path) -> Optional[str]:
    """None when a resolved source still reads as it did and what was read still states `value`; otherwise why not. A
    source pinned to a commit cannot drift, but its commit and file are re-read so a hand-made resolution cannot pass."""
    res = source.resolved
    if res is None:
        return "never resolved"
    if source.kind == "run":
        try:
            read = json.loads(res.text)
        except ValueError:
            read = None
        if not equal(value, read):
            return f"the value {render(value)} is not what was read from {source.path} ({res.text})"
    elif not states(source.quote or res.text, value):
        return f"the value {render(value)} is not stated by what was read from {source.path} ({res.text!r})"
    try:
        if source.kind == "file" and source.commit:
            data, _ = at_commit(full_path(root, str(source.path)), source.commit)
        elif source.kind == "run":
            data = run_json_path(root, str(source.path)).read_bytes()
        else:
            data = full_path(root, str(source.path)).read_bytes()
    except Unresolved as u:
        return u.reason
    except OSError:
        return f"{source.path} is gone"
    if sha256_bytes(data) != res.sha256:
        return f"{source.path} at {source.commit[:12]} does not match what was verified" if source.commit else f"{source.path} changed since it was verified"
    return None


def file_ref(root: Path, path: str) -> dict:
    """A file named by an evidence record: its path (relative to the directory holding .rb/), hash and size."""
    p = full_path(root, path)
    if not p.is_file():
        raise FileNotFoundError(path)
    data = p.read_bytes()
    return {"path": path, "sha256": sha256_bytes(data), "bytes": len(data)}
