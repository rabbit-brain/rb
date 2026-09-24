"""Resolving a source: the one way a setting becomes `verified`.

"Verified" means: this key has this value at this commit. Nothing here guesses, and every rule errs toward "not
stated", because a setting left provisional by mistake costs a second look and a setting marked verified by mistake
costs a wrong result.

- **Structured files** (YAML, JSON, TOML) are read by key path: `configs/train.yaml#optim.lr`. The value at the key is
  compared with the setting's. A `path:LINE` on a YAML file is turned into the key path on that line, so moving lines
  never matters afterwards.
- **Other text** (scripts, argparse defaults, papers saved as text): a line states a value only if it is not a comment,
  names the setting (its last dotted part, or the `term` given, such as "learning rate"), and holds the value as a
  standalone token. A `quote` points at the exact text instead of naming the setting; its line still must not be a
  comment. A failure lists the candidate lines.
- **Run sources** read a JSON pointer in a run's `record.json`.

How values compare: a number is a standalone token compared exactly (an integer only by the same integer, a float by the
same double: `1e-4` is `0.0001`, but `resnet50` does not state 50 and `1700000001` does not state 1700000000); text is a
whole token (`adam` is not stated by `adamw`); a list is a bracketed or comma-separated group with the same elements in
the same order.
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
NUMBER_TOKEN = re.compile(rf"(?<![\w.\-+]){NUM}(?!\d|\.\d|[-+]\d|,\d)")   # a full stop after it ends a sentence, not the number
THOUSANDS = re.compile(r"(?<![\w.,\-+])[-+]?\d{1,3}(?:,\d{3})+(?![\w.,])")
BOOL = re.compile(r"(?<![\w.\-])(true|false|True|False|TRUE|FALSE)(?![\w.\-])")
GROUP = re.compile(r"[\[\(\{]([^\[\]\(\)\{\}]*)[\]\)\}]")
COMMENT = re.compile(r"^\s*(#|//|%|;|\*|/\*)")
STRUCTURED = {".yaml": "yaml", ".yml": "yaml", ".json": "json", ".toml": "toml"}
PROSE = {".txt", ".md", ".rst", ".tex", ".html", ".htm", ""}      # files where '#' and '//' are text, not the start of a comment
TRAILING_COMMENT = re.compile(r"\s+(?:#|//).*$")
UNTRACKED_HASH_LIMIT = 16 * 1024 * 1024


class Unresolved(Exception):
    """The source was read and does not state the value, or could not be read. `found` is set when the source was read
    and states a different value (a conflict, not a missing value). `candidates` lists lines worth a look."""

    def __init__(self, code: str, reason: str, found: Any = None, differs: bool = False, candidates: Optional[list[dict]] = None) -> None:
        super().__init__(reason)
        self.code, self.reason, self.found, self.differs = code, reason, found, differs
        self.candidates = candidates or []


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


def git_top(path: Path) -> Optional[Path]:
    top = git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel")
    return Path(top.strip()).resolve() if top else None


# ---------------------------------------------------------------- repository state for receipts


def git_state(root: Path, exclude: str = ".rb") -> Optional[dict]:
    """The repository at this moment: the commit (None before the first commit), the branch, whether the tree had
    uncommitted changes, and a hash covering the tracked diff and the untracked files. The research state's own
    directory is left out: recording evidence writes to it."""
    top = git_top(root)
    if top is None:
        return None
    head = git(root, "rev-parse", "--verify", "-q", "HEAD")
    try:
        skip = (root.resolve() / exclude).relative_to(top).as_posix()
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


def _in_state(root: Path, path: str) -> bool:
    try:
        return (root / ".rb").resolve() in [(root / path).resolve(), *(root / path).resolve().parents]
    except OSError:
        return False


def inside_project(root: Path, path: str) -> bool:
    """A source must live in the project: under the directory holding .rb/, or in the git repository that holds it."""
    real = Path(os.path.realpath(full_path(root, path)))
    bases = [root.resolve()]
    top = git_top(root)
    if top is not None:
        bases.append(top)
    return any(real == b or b in real.parents for b in bases)


def _repo_and_rel(file: Path) -> Optional[tuple[Path, str]]:
    """The repository holding `file`, and the file's path inside it, whatever the ledger's own root is."""
    real = Path(os.path.realpath(file))
    top = git_top(real.parent if real.parent.exists() else file.parent)
    if top is None:
        return None
    try:
        return top, real.relative_to(top).as_posix()
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
    """HEAD of the file's own repository when the working-tree file is tracked and identical to HEAD's copy."""
    loc = _repo_and_rel(file)
    if loc is None:
        return None
    top, rel = loc
    head = git(top, "rev-parse", "--verify", "-q", "HEAD")
    if head is None:
        return None
    return head.strip() if git_bytes(top, "show", f"HEAD:{rel}") == data else None


def read_file(root: Path, path: str, commit: Optional[str]) -> tuple[bytes, Optional[str]]:
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


# ---------------------------------------------------------------- structured files


def structured_kind(path: str) -> Optional[str]:
    return STRUCTURED.get(Path(path).suffix.lower())


def parse_structured(text: str, kind: str, name: str) -> Any:
    try:
        if kind == "json":
            return json.loads(text)
        if kind == "toml":
            try:
                import tomllib
            except ModuleNotFoundError:  # pragma: no cover - Python 3.10
                import tomli as tomllib  # type: ignore[no-redef]
            return tomllib.loads(text)
        try:
            import yaml
        except ModuleNotFoundError:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"reading {name} needs PyYAML: pip install \"rabbit-brain[yaml]\"")
        return yaml.load(text, Loader=_yaml_loader())
    except Unresolved:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser's error is "this file does not parse"
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{name} does not parse as {kind.upper()}: {str(exc).splitlines()[0][:160]}")


_LOADER: Any = None


def _yaml_loader() -> Any:
    """PyYAML's safe loader, reading numbers the way YAML 1.2 and every training config mean them: `1e-4` is a float
    (YAML 1.1 wants a dot), and a date stays the text it is."""
    global _LOADER
    if _LOADER is None:
        import yaml

        class Loader(yaml.SafeLoader):
            pass

        Loader.yaml_implicit_resolvers = {ch: [(tag, rx) for tag, rx in rs if tag != "tag:yaml.org,2002:timestamp"]
                                          for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()}
        Loader.add_implicit_resolver("tag:yaml.org,2002:float", re.compile(r"^[-+]?(?:[0-9][0-9_]*(?:\.[0-9_]*)?|\.[0-9_]+)[eE][-+]?[0-9]+$"),
                                     list("-+0123456789."))
        _LOADER = Loader
    return _LOADER


def key_steps(key: str) -> list[str]:
    """`optim.lr` or `/optim/lr` (a JSON pointer, for keys that themselves contain dots)."""
    if key.startswith("/"):
        return [s.replace("~1", "/").replace("~0", "~") for s in key[1:].split("/")]
    return key.split(".")


def get_key(doc: Any, key: str) -> Any:
    cur = doc
    for step in key_steps(key):
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


def unwrap_wandb(doc: Any) -> Any:
    """A W&B config.yaml stores each value as {desc, value} and adds _wandb bookkeeping."""
    if isinstance(doc, dict) and any(isinstance(v, dict) and set(v) <= {"desc", "value"} and "value" in v for v in doc.values()):
        return {k: (v["value"] if isinstance(v, dict) and "value" in v and set(v) <= {"desc", "value"} else v)
                for k, v in doc.items() if not str(k).startswith("_wandb") and k != "wandb_version"}
    return doc


def flatten(doc: Any, prefix: str = "") -> dict[str, Any]:
    """{"optim": {"lr": 1e-4}} -> {"optim.lr": 1e-4}. Lists of scalars stay values."""
    out: dict[str, Any] = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(flatten(v, key))
            else:
                out[key] = v
    elif prefix:
        out[prefix] = doc
    return out


def yaml_line_of(text: str, key: str) -> Optional[int]:
    """The line holding the value at `key` in a YAML file, from the parser's own positions."""
    try:
        import yaml
        node = yaml.compose(text)
    except Exception:  # noqa: BLE001
        return None
    for step in key_steps(key):
        if isinstance(node, yaml.MappingNode):
            node = next((v for k, v in node.value if getattr(k, "value", None) == step), None)
        elif isinstance(node, yaml.SequenceNode) and step.isdigit() and int(step) < len(node.value):
            node = node.value[int(step)]
        else:
            return None
        if node is None:
            return None
    return node.start_mark.line + 1


def key_names(key: str, name: str, term: Optional[str] = None) -> bool:
    """Whether a config key is the setting's own: the same path, the same last segment (`optim.lr` for `lr`), or the
    source's --term. A line that happens to hold the value under another key (`weight_decay: 1e-4` for lr) is not."""
    last = name.split(".")[-1].lower()
    steps = [x.lower() for x in key_steps(key)]
    return ".".join(steps) == name.lower() or steps[-1] == last or (bool(term) and key_token_in(" ".join(key_steps(key)), str(term)))


def yaml_key_at(text: str, line: int) -> Optional[str]:
    """The key path whose scalar value sits on `line` of a YAML file, or None."""
    try:
        import yaml
        root = yaml.compose(text)
    except Exception:  # noqa: BLE001
        return None
    found: list[str] = []

    def walk(node: Any, path: list[str]) -> None:
        if found:
            return
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, [*path, str(getattr(k, "value", ""))])
        elif isinstance(node, yaml.SequenceNode):
            if node.start_mark.line + 1 == line and path and all(isinstance(x, yaml.ScalarNode) for x in node.value):
                found.append(".".join(path))
                return
            for i, v in enumerate(node.value):
                walk(v, [*path, str(i)])
        elif isinstance(node, yaml.ScalarNode) and node.start_mark.line + 1 == line and path:
            found.append(".".join(path))
    if root is not None:
        walk(root, [])
    return found[0] if found else None


# ---------------------------------------------------------------- "stated"


def _parse_number(token: str) -> Any:
    t = token.replace(",", "")
    if re.fullmatch(r"[-+]?\d+", t):
        return int(t)
    return float(t)


def _num_equal(value: Any, found: Any) -> bool:
    """An integer equals only an integral number of the same value; a float equals the same double."""
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


def _nearest_number(text: str, token: str) -> Any:
    """The number closest to where the line names the setting: in 'the learning rate is 0.01 and weight decay 0.0001',
    weight decay's is 0.0001, and in 'default=0.01, help="try 0.05"' the setting's is 0.01, not the one in the help."""
    names = [m.span() for m in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text, flags=re.IGNORECASE)]
    spans = []
    for m in NUMBER_TOKEN.finditer(text):
        if m.start() >= 2 and text[m.start() - 1] == "," and text[m.start() - 2].isdigit():
            continue
        try:
            spans.append((m.span(), _parse_number(m.group(0))))
        except ValueError:
            pass
    spans += [(m.span(), _parse_number(m.group(0))) for m in THOUSANDS.finditer(text)]
    best = None
    for (a, b), n in spans:
        for (x, y) in names:
            gap = a - y if a >= y else x - b          # a number after the name wins a tie
            key = (gap, 0 if a >= y else 1)
            if gap >= 0 and (best is None or key < best[0]):
                best = (key, n)
    return None if best is None else best[1]


FUNCTION_WORDS = {"the", "and", "for", "with", "our", "this", "that", "are", "was", "were", "from", "into", "all", "use", "used", "using", "set", "value"}


def _text_token(value: str, text: str) -> bool:
    if not value.strip():
        return False
    return re.search(rf"(?<![\w.\-]){re.escape(value)}(?![\w.\-])", text) is not None


def _item_is(item: str, value: Any) -> bool:
    s = item.strip().strip("'\"").strip()
    if isinstance(value, bool):
        return s.lower() == str(value).lower()
    if isinstance(value, (int, float)):
        return bool(re.fullmatch(NUM, s)) and _num_equal(value, _parse_number(s))
    if isinstance(value, str):
        return bool(value.strip()) and s == value
    return False


def _list_stated(text: str, value: list) -> bool:
    candidates = [m.group(1) for m in GROUP.finditer(text)]
    candidates.append(re.split(r"[:=]", text)[-1])
    for cand in candidates:
        items = cand.split(",")
        if items and not items[-1].strip():
            items = items[:-1]
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


def as_value(read: Any) -> Any:
    """A value read from a structured file, as it is: text stays text ("11.10" is not 11.1). YAML's `1e-4` is read as a
    number by the loader itself."""
    return read


def equal(a: Any, b: Any) -> bool:
    """A setting's value against a value read from a file or run: numbers by the rule above, lists element by element in
    order, everything else exactly: the text "12.4" is stated by the text "12.4" and not by the number."""
    if isinstance(a, list) or isinstance(b, list):
        return isinstance(a, list) and isinstance(b, list) and len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _num_equal(a, b)
    return a == b


def key_token_in(line: str, token: str) -> bool:
    """The setting is named on the line: `lr` in `lr: 0.001`, `--lr 0.001`, `optim.lr = 0.001`; a term such as
    "learning rate" anywhere, ignoring case."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", line, flags=re.IGNORECASE) is not None


def is_comment(line: str) -> bool:
    return bool(COMMENT.match(line))


def pointer_get(doc: Any, pointer: str) -> Any:
    """RFC 6901. Raises KeyError naming the step that does not exist."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    return get_key(doc, pointer)


def run_json_path(root: Path, path: str) -> Path:
    p = full_path(root, path)
    return p / "record.json" if p.is_dir() else p


def render(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


# ---------------------------------------------------------------- resolving


def resolve(source: Source, value: Any, root: Path, name: str, previous: Optional[Resolution] = None, named: bool = True) -> Resolution:
    """Read the source and confirm it states `value` for the setting `name`. Returns what was read; raises Unresolved
    otherwise, including for anything unexpected while reading, so one bad source never stops the others being checked."""
    try:
        return _resolve(source, value, root, name, previous, named)
    except Unresolved:
        raise
    except (OSError, ValueError, KeyError, UnicodeError) as exc:
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.label()} could not be read: {type(exc).__name__}: {exc}")


def _resolve(source: Source, value: Any, root: Path, name: str, previous: Optional[Resolution], named: bool = True) -> Resolution:
    """`named`: the source has to name the setting (its name or --term) as well as state the value. A cited claim's
    number is exempt: a paper's table row states 1.43 without the word EPE on it."""
    if not source.checkable():
        if source.kind == "file":
            raise Unresolved("E_SOURCE_UNVERIFIABLE", "a file source needs a key path (file#key), a line (file:LINE) or a quote for rb to check it")
        raise Unresolved("E_SOURCE_UNVERIFIABLE", f"a {source.kind} source records where the value came from but rb cannot check it; save the page in the repository and point at it")
    if source.path and not inside_project(root, str(source.path)):
        raise Unresolved("E_SOURCE_OUTSIDE", f"{source.path} is outside the project, so nobody else can check it: copy it into the repository and commit it")
    if source.path and _in_state(root, str(source.path)):
        raise Unresolved("E_SOURCE_OUTSIDE", f"{source.path} is rb's own record in .rb/, which cannot be where a value comes from")
    step = (key_steps(str(source.pointer))[-1:] or [""])[0] if source.kind == "run" else None
    if named and source.kind == "run" and step and not key_names(str(source.pointer), name, source.term):
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path}#{source.pointer} is {step!r}, not {name}: point at {name}'s own entry, or give --term with the name the file uses")
    if source.kind == "run":
        p = run_json_path(root, str(source.path))
        if not p.is_file():
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path} does not exist")
        data = p.read_bytes()
        doc = parse_structured(data.decode("utf-8", errors="replace"), "json", str(source.path))
        try:
            got = pointer_get(doc, str(source.pointer))
        except KeyError as exc:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{p} has nothing at {source.pointer} (no '{exc.args[0]}')")
        if not equal(value, got):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path}{source.pointer} is {json.dumps(got)}, not {render(value)}", found=got, differs=True)
        return Resolution(at=now(), commit=committed_as(p, data), sha256=sha256_bytes(data), text=json.dumps(got)[:400], read=got)
    data, commit = read_file(root, str(source.path), source.commit)
    text = data.decode("utf-8", errors="replace")
    kind = structured_kind(str(source.path))
    if source.key:
        if kind is None:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"a key path needs a YAML, JSON or TOML file; {source.path} is none of those. Use {source.path}:LINE or --quote")
        if named and not key_names(source.key, name, source.term):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path}#{source.key} is not {name}: point at {name}'s own key, or give --term with the name the file uses")
        doc = parse_structured(text, kind, str(source.path))
        try:
            got = get_key(doc, source.key)
        except KeyError as exc:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path} has no key {source.key} (no '{exc.args[0]}')")
        if isinstance(got, (dict, list)) and not isinstance(value, list):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path}#{source.key} is a section, not a value")
        if not equal(value, got):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path}#{source.key} is {render(as_value(got))}, not {render(value)}", found=as_value(got), differs=True)
        line = yaml_line_of(text, source.key) if kind == "yaml" else None
        return Resolution(at=now(), commit=commit, sha256=sha256_bytes(data), line=line, text=f"{source.key}: {render(as_value(got))}"[:400], read=as_value(got))
    lines = split_lines(text)
    if source.term is not None and (not re.search(r"[A-Za-z]", source.term) or source.term.strip().lower() in FUNCTION_WORDS):
        raise Unresolved("E_SOURCE_UNRESOLVED", f"--term {source.term!r} names nothing in particular: give the words the text uses for this setting, e.g. \"weight decay\"")
    token = source.term or name.split(".")[-1]
    if source.line is not None:
        found_line, found_text = _line_or_moved(source, lines, previous)
    else:
        idx = text.find(str(source.quote))
        if idx < 0:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"the quote is not in {source.path}")
        found_line = text.count("\n", 0, idx) + 1
        found_text = lines[found_line - 1] if found_line <= len(lines) else str(source.quote)
    where = f"{source.path}:{found_line}"
    if is_comment(found_text):
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} is a comment; a comment does not set a value", candidates=_candidates(lines, value, token))
    code = Path(str(source.path)).suffix.lower() not in PROSE
    setting_text = TRAILING_COMMENT.sub("", found_text) if code else found_text     # 'lr = 1e-4  # the paper used 3e-4' sets 1e-4
    if source.quote:
        if source.quote not in found_text and source.line is not None:
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} does not contain the quote; it reads: {found_text.strip()[:200]}")
        if named and not key_token_in(setting_text, token):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} does not name {token!r}: a quote has to be about this setting. Give --term with the "
                             f"word the text uses for it", candidates=_candidates(lines, value, token))
        haystack = source.quote if (not code or source.quote in setting_text) else setting_text
    else:
        found_text = setting_text
        if not key_token_in(found_text, token):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} does not name {token!r}; it reads: {found_text.strip()[:200]}. "
                             f"Point at the line that sets it, give --term with the word the file uses, or --quote the exact text",
                             candidates=_candidates(lines, value, token))
        haystack = found_text
    if not states(haystack, value):
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} does not state {render(value)}; it reads: {haystack.strip()[:200]}",
                         differs=key_token_in(found_text, token), candidates=_candidates(lines, value, token))
    if not source.quote and isinstance(value, (int, float)) and not isinstance(value, bool):
        near = _nearest_number(found_text, token)       # the value must be the setting's own number on the line, not another one on it
        if near is not None and not _num_equal(value, near):
            raise Unresolved("E_SOURCE_UNRESOLVED", f"{where} gives {token} {render(near)}, not {render(value)}; it reads: {found_text.strip()[:200]}",
                             found=near, differs=True, candidates=_candidates(lines, value, token))
    return Resolution(at=now(), commit=commit, sha256=sha256_bytes(data), line=found_line, text=(found_text.strip() or haystack.strip())[:400])


def _line_or_moved(source: Source, lines: list[str], previous: Optional[Resolution]) -> tuple[int, str]:
    """The stated line, or, when a line was inserted above it since it was verified, the same text wherever it now is."""
    assert source.line is not None
    if source.line <= len(lines):
        text = lines[source.line - 1]
        if previous is None or text.strip() == previous.text.strip() or not previous.text:
            return source.line, text
    if previous is not None and previous.text:
        matches = [i + 1 for i, line in enumerate(lines) if line.strip() == previous.text.strip()]
        if len(matches) == 1:
            return matches[0], lines[matches[0] - 1]
    if source.line > len(lines):
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{source.path} has {len(lines)} lines, not {source.line}")
    return source.line, lines[source.line - 1]


def _candidates(lines: list[str], value: Any, token: str) -> list[dict]:
    out = []
    for i, line in enumerate(lines, 1):
        if is_comment(line):
            continue
        if key_token_in(line, token) or (not isinstance(value, str) and states(line, value)):
            out.append({"line": i, "text": line.strip()[:160]})
        if len(out) >= 5:
            break
    return out


def recheck(source: Source, value: Any, root: Path, name: str, named: bool = True) -> tuple[str, str]:
    """How a verified source reads now: ("ok", ""), ("moved", why) when the file changed but still states the value
    (non-blocking; re-verifying refreshes it), ("stale", why) when it no longer states it or cannot be read, or
    ("conflict", why) when it now states a different value. The source is always re-read, at its commit when pinned:
    a matching file hash is not enough, so a hand-made resolution cannot pass."""
    res = source.resolved
    if res is None:
        return "stale", "never verified"
    if source.kind == "run" or source.key:
        if not equal(value, res.read):
            return "stale", f"the value {render(value)} is not what was read from {source.label()} ({res.text})"
    elif not states(source.quote or res.text, value):
        return "stale", f"the value {render(value)} is not stated by what was read from {source.label()} ({res.text!r})"
    try:
        if source.kind == "run":
            data = run_json_path(root, str(source.path)).read_bytes()
        elif source.commit:
            data, _ = at_commit(full_path(root, str(source.path)), source.commit)
        else:
            data = full_path(root, str(source.path)).read_bytes()
    except Unresolved as u:
        return "stale", u.reason
    except OSError:
        return "stale", f"{source.path} is gone"
    same_bytes = sha256_bytes(data) == res.sha256
    if source.commit and not same_bytes:
        return "stale", f"{source.path} at {source.commit[:12]} does not match what was verified"
    try:
        resolve(source, value, root, name, previous=res, named=named)
    except Unresolved as u:
        if same_bytes:
            return "stale", f"{source.label()} is the file rb verified, yet it does not state {render(value)}: the record of what was read was not made by rb"
        if u.differs:
            return "conflict", f"{source.label()} now says {render(u.found) if u.found is not None else 'something else'}: {u.reason}"
        return "stale", u.reason
    if same_bytes:
        return "ok", ""
    return "moved", f"{source.path} changed since it was verified and still states {render(value)}; rb spec verify refreshes it"


def file_ref(root: Path, path: str) -> dict:
    """A file named by an evidence record: its path (relative to the directory holding .rb/), hash and size."""
    p = full_path(root, path)
    if not p.is_file():
        raise FileNotFoundError(path)
    data = p.read_bytes()
    return {"path": path, "sha256": sha256_bytes(data), "bytes": len(data)}


def load_config(path: Path) -> Any:
    """A run's resolved config as a dict: Hydra's config.yaml, a W&B config.yaml, Lightning's hparams.yaml, any JSON or
    TOML."""
    kind = structured_kind(str(path))
    if kind is None:
        raise Unresolved("E_SOURCE_UNRESOLVED", f"{path} is not YAML, JSON or TOML")
    doc = parse_structured(path.read_text(encoding="utf-8", errors="replace"), kind, str(path))
    return unwrap_wandb(doc)
