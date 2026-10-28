"""Rabbit Brain YAML: the YAML every Rabbit Brain record is written in, and the only YAML rb reads as a record.

A record is for people to read and for machines to operate on, so it is YAML, and the YAML is constrained hard:
mappings, lists, text, numbers, true/false, null and comments. Nothing else: no anchors or aliases, no tags, no merge
keys, no directives, one document per file. A plain value that YAML 1.1 and YAML 1.2 parsers read differently is
refused, with the spelling to write instead (`08`, `1e3`, `.inf`, `2026-09-30`, `no`, `10:30`), so a file rb accepts
reads the same in PyYAML, ruamel, js-yaml, go-yaml and every other parser, and a date is always quoted text.

`dumps` writes the canonical form, which is what `rb fmt` produces: two-space indents, block style, text plain when
plain is safe and double-quoted when not, multi-line text as `|` blocks, and floats every parser reads as floats.
Comments belong to the reader: `load` returns them anchored to the key or list item they sit on, and `dumps` puts them
back, so rb rewriting a record never drops what someone wrote in it.

`canonical_json` is the machine form: sorted keys and no whitespace, the text every content hash is taken of. Two
files that differ only in comments, key order or style hold the same content, and hash the same.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

__all__ = ["YAMLError", "Comments", "Document", "load", "loads", "dumps", "fmt", "canonical_json", "content_sha"]

MAX_DEPTH = 64

_INDICATORS = frozenset("-?:,[]{}#&*!|>'\"%@`")
_NULL = frozenset(("", "~", "null", "Null", "NULL"))
_TRUE = frozenset(("true", "True", "TRUE"))
_FALSE = frozenset(("false", "False", "FALSE"))
_BOOL11 = frozenset(("yes", "Yes", "YES", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF", "y", "Y", "n", "N"))
_INT = re.compile(r"[-+]?(?:0|[1-9][0-9]*)")
# The floats YAML 1.1 (PyYAML) and YAML 1.2 both read, as the same number: a dot, and a signed exponent if any.
_FLOAT = re.compile(r"[-+]?[0-9]+\.[0-9]*(?:[eE][-+][0-9]+)?|\.[0-9]+(?:[eE][-+][0-9]+)?")
# Every other spelling of a number some parser reads as one: leading zeros, underscores, hex, octal, binary, an
# exponent without a dot or a sign, a sign before a bare dot, sexagesimal (10:30), infinities and nan.
_NUMERIC = re.compile(r"""[-+]?[0-9][0-9_]*
    |[-+]?0[bBoOxX][0-9a-fA-F_]+
    |[-+]?(?:[0-9][0-9_]*\.[0-9_]*|\.[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)?
    |[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+
    |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?
    |[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)""", re.X)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
                        r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)?")
_DATE_PREFIX = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}")
# Characters a YAML file cannot hold as they are: C0 controls but tab and newline, DEL and C1 controls, the Unicode
# line and paragraph separators (line breaks to YAML 1.1), byte-order marks, non-characters and lone surrogates.
_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f  ﻿￾￿\ud800-\udfff]")
_COMMENT = re.compile(r"[ \t]#")
_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
            "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": " ", "P": " "}
_HEX = {"x": 2, "u": 4, "U": 8}


class YAMLError(ValueError):
    """Why a text is not Rabbit Brain YAML: where (1-based line and column), what, and what to write instead."""

    def __init__(self, message: str, line: int = 0, col: int = 0, fix: str = "", source: str = "") -> None:
        self.message, self.line, self.col, self.fix, self.source = message, line, col, fix, source
        super().__init__(self.__str__())

    def __str__(self) -> str:
        where = f"{self.source or '<text>'}:{self.line}:{self.col}: " if self.line else (f"{self.source}: " if self.source else "")
        return where + self.message + (f"; {self.fix}" if self.fix else "")


class _Ambiguous(Exception):
    def __init__(self, message: str, fix: str) -> None:
        self.message, self.fix = message, fix
        super().__init__(message)


@dataclass
class Comments:
    """What someone wrote around the data: comment lines (and single blank lines, as "") above a key or list item, a
    comment at the end of its line, and comments after everything. A path names keys by their text, and list items by
    their `id`, or their `name`, when every item of the list has a distinct one, else by position."""
    before: dict[tuple, list[str]] = field(default_factory=dict)
    after: dict[tuple, str] = field(default_factory=dict)
    footer: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.before or self.after or self.footer)


@dataclass
class Document:
    data: Any
    comments: Comments


# ---------------------------------------------------------------- reading a plain value


def _resolve(s: str) -> Any:
    """The value of a plain (unquoted) scalar, read the way every YAML parser reads it, or _Ambiguous."""
    if s in _NULL:
        return None
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    c = s[0]
    if c in "0123456789+-.":
        if _INT.fullmatch(s):
            return int(s)
        if _FLOAT.fullmatch(s):
            x = float(s)
            if x != x or x in (float("inf"), float("-inf")):       # too large to hold: infinity, which this format does not have
                raise _Ambiguous(f"{s} is too large to keep as a number", f'write a number up to about 1.8e+308, or quote it for text: "{s}"')
            return x
        if _NUMERIC.fullmatch(s):
            raise _Ambiguous(f"{s} is a number to some YAML parsers and not to others",
                             f'write it as a plain decimal number (7, 0.5, 1.0e+3), or quote it for text: "{s}"')
        if _TIMESTAMP.fullmatch(s):
            raise _Ambiguous(f"{s} is a date to some YAML parsers and text to others", f'quote it: "{s}"')
    elif s in _BOOL11:
        raise _Ambiguous(f"{s} is true or false to some YAML parsers and text to others", f'write true or false, or quote it for text: "{s}"')
    elif s in ("<<", "="):
        raise _Ambiguous(f"{s} has a special meaning to some YAML parsers", f'quote it: "{s}"')
    return s


def _item_scheme(items: list) -> Optional[str]:
    """How the items of a list are named in a comment's path: by `id`, by `name`, or (None) by position."""
    for key in ("id", "name"):
        vals = [x.get(key) if isinstance(x, dict) else None for x in items]
        if vals and all(isinstance(v, str) for v in vals) and len(set(vals)) == len(vals):
            return key
    return None


# ---------------------------------------------------------------- the parser


def _is_dash(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _cut_comment(s: str) -> tuple[str, Optional[str]]:
    m = _COMMENT.search(s)
    if m is None:
        return s, None
    return s[:m.start()], s[m.start() + 1:].rstrip()


class _Parser:
    def __init__(self, text: str, source: str) -> None:
        self.source = source
        if text.startswith("﻿"):
            text = text[1:]
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        bad = _BAD.search(text)
        if bad is not None:
            line = text.count("\n", 0, bad.start()) + 1
            col = bad.start() - (text.rfind("\n", 0, bad.start()) + 1) + 1
            o = ord(bad.group())
            raise YAMLError(f"character U+{o:04X} cannot stand as it is in a YAML file", line, col,
                            f'write it as an escape inside double quotes ("\\u{o:04x}")', source)
        self.lines = text.split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()                     # the last newline ends the last line; it is not an empty line
        self.n = len(self.lines)
        self.i = 0
        self.comments = Comments()
        self.pending: list[str] = []
        self.started = False
        self.depth = 0

    def err(self, message: str, line: int, col: int, fix: str = "") -> YAMLError:
        return YAMLError(message, line, col, fix, self.source)

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.err(f"nested more than {MAX_DEPTH} levels deep", self.i + 1, 1)

    # -- lines

    def indent(self, k: int) -> int:
        line = self.lines[k]
        n = len(line) - len(line.lstrip(" "))
        if n < len(line) and line[n] == "\t":
            raise self.err("a tab is not indentation in YAML", k + 1, n + 1, "indent with spaces")
        return n

    def blank(self, k: int) -> bool:
        return self.lines[k].strip(" \t") == ""

    def is_start(self, k: int) -> bool:
        line = self.lines[k]
        return line == "---" or line.startswith(("--- ", "---\t"))

    def is_end(self, k: int) -> bool:
        line = self.lines[k]
        return line == "..." or line.startswith(("... ", "...\t"))

    def is_marker(self, k: int) -> bool:
        return self.is_start(k) or self.is_end(k)

    def skip(self) -> None:
        """Past blank lines and comment lines, keeping the comments, and one blank line in a row, for the next node."""
        while self.i < self.n:
            s = self.lines[self.i].strip(" \t")
            if s == "":
                if (self.pending or self.started) and (not self.pending or self.pending[-1] != ""):
                    self.pending.append("")
            elif s[0] == "#":
                self.pending.append(s.rstrip())
            else:
                return
            self.i += 1

    def attach(self, path: tuple) -> None:
        self.started = True
        if self.pending:
            self.comments.before.setdefault(path, []).extend(self.pending)
            self.pending = []

    # -- structure

    def document(self) -> Any:
        self.skip()
        if self.i < self.n and self.lines[self.i].startswith("%"):
            raise self.err("directives (% lines) are not part of Rabbit Brain YAML", self.i + 1, 1, "remove the line")
        if self.i < self.n and self.is_start(self.i):
            rest = self.lines[self.i][3:].strip(" \t")
            if rest and rest[0] != "#":
                raise self.err("the document starts on the line after ---", self.i + 1, 5, "move it down a line")
            if rest:
                self.pending.append(rest)
            self.i += 1
        data = self.node(-1, ())
        self.skip()
        if self.i < self.n:
            if self.is_end(self.i):
                rest = self.lines[self.i][3:].strip(" \t")
                if rest and rest[0] != "#":
                    raise self.err("nothing may follow ... on its line", self.i + 1, 5)
                self.i += 1
                self.skip()
                if self.i < self.n:
                    raise self.err("one document per file: nothing may follow ...", self.i + 1, 1, "put the second document in a file of its own")
            elif self.is_start(self.i):
                raise self.err("one document per file", self.i + 1, 1, "put the second document in a file of its own")
            else:
                k = self.i
                raise self.err("this line does not fit the structure above it", k + 1, self.indent(k) + 1,
                               "check its indentation against the lines above")
        footer = [c for c in self.pending]
        while footer and footer[-1] == "":
            footer.pop()
        while footer and footer[0] == "":
            footer.pop(0)
        self.comments.footer = footer
        self.comments = _identify(self.comments, data)
        return data

    def node(self, parent: int, path: tuple) -> Any:
        """A block node on the lines ahead, indented more than `parent`: a mapping, a list, or a value on its own
        lines. None when there is none (the key had no value)."""
        self.skip()
        if self.i >= self.n or self.is_marker(self.i):
            return None
        ind = self.indent(self.i)
        if ind <= parent:
            return None
        text = self.lines[self.i][ind:]
        if _is_dash(text):
            return self.seq(ind, path)
        if self.key_end(text) >= 0:
            return self.map(ind, path)
        self.attach(path)
        return self.inline(self.i, ind, parent, path)

    def key_end(self, text: str) -> int:
        """The index of the ':' that makes `text` a `key: value` line, or -1."""
        if not text or text[0] in "[{|>#":
            return -1
        if text[0] in "\"'":
            end = _quoted_end(text)
            if end < 0:
                return -1
            j = end
            while j < len(text) and text[j] in " \t":
                j += 1
            if j < len(text) and text[j] == ":" and (j + 1 == len(text) or text[j + 1] in " \t"):
                return j
            return -1
        j = 0
        while True:
            j = text.find(":", j)
            if j < 0:
                return -1
            if j + 1 == len(text) or text[j + 1] in " \t":
                m = _COMMENT.search(text, 0, j)
                return -1 if m is not None else j
            j += 1

    def key_text(self, raw: str, k: int, col: int) -> str:
        if raw and raw[0] in "\"'":
            value, end = _quoted_line(raw, lambda m, c: self.err(m, k + 1, col + c + 1))
            if raw[end:].strip(" \t"):
                raise self.err("unexpected text after a quoted key", k + 1, col + end + 1)
            return value
        if not raw:
            raise self.err("a key is missing before ':'", k + 1, col + 1, "write the key, or quote the text if ':' is part of it")
        if raw[0] in "&*!":
            what = {"&": "anchors", "*": "aliases", "!": "tags"}[raw[0]]
            raise self.err(f"{what} are not part of Rabbit Brain YAML", k + 1, col + 1, "write the key as plain text")
        if raw[0] in _INDICATORS and not (raw[0] in "-?:" and len(raw) > 1 and raw[1] not in " \t"):
            raise self.err(f"a key cannot start with {raw[0]!r} unquoted", k + 1, col + 1, f'quote it: "{raw}"')
        try:
            value = _resolve(raw)
        except _Ambiguous as a:
            raise self.err(a.message, k + 1, col + 1, a.fix)
        if not isinstance(value, str):
            raise self.err(f"a key is text, and {raw} reads as {json.dumps(value)}", k + 1, col + 1, f'quote it: "{raw}"')
        return value

    def map(self, ind: int, path: tuple) -> dict:
        self.enter()
        out: dict = {}
        while True:
            self.skip()
            if self.i >= self.n or self.is_marker(self.i):
                break
            k = self.i
            li = self.indent(k)
            if li < ind:
                break
            if li > ind:
                raise self.err("this line is indented more than the keys beside it", k + 1, li + 1,
                               "line it up with its sibling keys, or put it under a key that ends with ':'")
            text = self.lines[k][ind:]
            if _is_dash(text):
                raise self.err("a list item where a key was expected", k + 1, ind + 1, "indent the list under the key it belongs to")
            if text == "?" or text.startswith(("? ", "?\t")):
                raise self.err("complex keys (? ) are not part of Rabbit Brain YAML", k + 1, ind + 1, "write the key as text on one line: quote it")
            colon = self.key_end(text)
            if colon < 0:
                raise self.err("expected a key and ':'", k + 1, ind + 1, "write key: value, or quote the text")
            key = self.key_text(text[:colon].rstrip(" \t"), k, ind)
            if key in out:
                raise self.err(f"the key {key!r} appears twice", k + 1, ind + 1, "keep one")
            kp = path + (key,)
            self.attach(kp)
            out[key] = self.value(k, ind + colon + 1, ind, kp)
        self.depth -= 1
        return out

    def value(self, k: int, col: int, ind: int, path: tuple) -> Any:
        """The value of `key:` whose colon ends at `col` on line k, in a mapping indented `ind`."""
        rest = self.lines[k][col:]
        s = rest.lstrip(" \t")
        if s == "" or s[0] == "#":
            if s:
                self.comments.after[path] = s.rstrip()
            self.i = k + 1
            self.skip()
            if self.i < self.n and not self.is_marker(self.i):
                li = self.indent(self.i)
                if li > ind:
                    return self.node(ind, path)
                if li == ind and _is_dash(self.lines[self.i][ind:]):
                    return self.seq(ind, path)
            return None
        return self.inline(k, col + len(rest) - len(s), ind, path)

    def seq(self, ind: int, path: tuple) -> list:
        self.enter()
        out: list = []
        while True:
            self.skip()
            if self.i >= self.n or self.is_marker(self.i):
                break
            k = self.i
            li = self.indent(k)
            if li < ind:
                break
            if li > ind:
                raise self.err("this line is indented more than the list items beside it", k + 1, li + 1,
                               "line it up with the other items, or put it under an item that ends with ':'")
            line = self.lines[k]
            if not _is_dash(line[ind:]):
                break                      # a key beside a list written flush with it ends the list
            ip = path + (len(out),)
            self.attach(ip)
            line = self.lines[k] = line[:ind] + " " + line[ind + 1:]     # the dash read as indentation: the rest is the item
            s = line[ind + 1:].lstrip(" ")
            if s == "" or s[0] == "#":
                if s:
                    self.comments.after[ip] = s.rstrip()
                self.i = k + 1
                out.append(self.node(ind, ip))
                continue
            c = len(line) - len(s)
            if _is_dash(s):
                out.append(self.seq(c, ip))
            elif self.key_end(s) >= 0:
                out.append(self.map(c, ip))
            else:
                out.append(self.inline(k, c, ind, ip))
        self.depth -= 1
        return out

    # -- scalars

    def inline(self, k: int, c: int, parent: int, path: tuple) -> Any:
        """A value starting at column c of line k, whose continuation lines are indented more than `parent`."""
        line = self.lines[k]
        ch = line[c]
        if ch in "|>":
            return self.block(k, c, parent, path)
        if ch == '"' or ch == "'":
            return self.quoted(k, c, parent, path)
        if ch in "[{":
            return self.flow(k, c, parent, path)
        if ch in "&*!":
            what = {"&": "anchors", "*": "aliases", "!": "tags"}[ch]
            fix = "drop the tag: text, numbers, true/false and null need none" if ch == "!" else "write the value out in full where it is used"
            raise self.err(f"{what} are not part of Rabbit Brain YAML", k + 1, c + 1, fix)
        nxt = line[c + 1:c + 2]
        if ch in "-?:" and nxt in ("", " ", "\t"):
            what = {"-": "a list cannot start on the line of its key", "?": "complex keys (? ) are not part of Rabbit Brain YAML",
                    ":": "a value cannot start with ': '"}[ch]
            raise self.err(what, k + 1, c + 1, "put a list on the lines below its key" if ch == "-" else f'quote it: "{line[c:].strip()}"')
        if ch in "@`%,]}":
            raise self.err(f"a value cannot start with {ch!r} unquoted", k + 1, c + 1, f'quote it: "{line[c:].strip()}"')
        return self.plain(k, c, parent, path)

    def check_plain(self, text: str, k: int, c: int) -> None:
        if ": " in text or ":\t" in text or text.endswith(":"):
            raise self.err("a plain value cannot contain ': ' or end with ':'", k + 1, c + 1, f'quote it: "{text}"')

    def plain(self, k: int, c: int, parent: int, path: tuple) -> Any:
        text, comment = _cut_comment(self.lines[k][c:])
        text = text.rstrip(" \t")
        self.check_plain(text, k, c)
        parts = [text]
        self.i = k + 1
        while comment is None:               # a plain value may go on over lines indented more than its key
            j, blanks = self.i, 0
            while j < self.n and self.blank(j):
                j, blanks = j + 1, blanks + 1
            if j >= self.n or self.is_marker(j):
                break
            lj = self.indent(j)
            if lj <= parent:
                break
            t = self.lines[j][lj:]
            if t.startswith("#"):
                break
            if _is_dash(t) or self.key_end(t) >= 0 or t[:2] in ("? ", ": "):
                raise self.err("this line is indented more than the keys beside it", j + 1, lj + 1,
                               "line it up with its sibling keys, or put it under a key that ends with ':'")
            t, comment = _cut_comment(t)
            t = t.rstrip(" \t")
            self.check_plain(t, j, lj)
            parts.append("\n" * blanks if blanks else " ")
            parts.append(t)
            self.i = j + 1
        if comment is not None:
            self.comments.after[path] = comment
        if len(parts) > 1:
            return "".join(parts)
        try:
            return _resolve(text)
        except _Ambiguous as a:
            raise self.err(a.message, k + 1, c + 1, a.fix)

    def quoted(self, k: int, c: int, parent: int, path: tuple) -> str:
        """A single- or double-quoted value, folded over lines the way YAML folds them."""
        q = self.lines[k][c]
        out: list[str] = []
        row, j = k, c + 1
        while True:
            line = self.lines[row]
            keep = len(out)
            escaped_break = False
            while j < len(line):
                ch = line[j]
                if ch == q:
                    if q == "'" and line[j + 1:j + 2] == "'":
                        out.append("'")
                        keep = len(out)
                        j += 2
                        continue
                    self.i = row + 1
                    self.after_value(row, j + 1, path)
                    return "".join(out)
                if ch == "\\" and q == '"':
                    if j + 1 == len(line):
                        escaped_break = True
                        j += 1
                        break
                    j, s = _escape(line, j, lambda m, cc, r=row: self.err(m, r + 1, cc + 1))
                    out.append(s)
                    keep = len(out)
                    continue
                out.append(ch)
                if ch not in " \t":
                    keep = len(out)
                j += 1
            if not escaped_break:
                del out[keep:]                   # a line's trailing whitespace is not content
            row += 1
            blanks = 0
            while row < self.n and self.blank(row):
                row, blanks = row + 1, blanks + 1
            if row >= self.n:
                style = "double" if q == '"' else "single"
                raise self.err(f"this {style}-quoted value never ends", k + 1, c + 1, f"close it with {q}")
            li = self.indent(row)
            if li <= parent or self.is_marker(row):
                raise self.err("a quoted value's next line must be indented more than its key", row + 1, li + 1,
                               "indent it, or keep the value on one line")
            if blanks:
                out.append("\n" * blanks)
            elif not escaped_break:
                out.append(" ")
            line = self.lines[row]
            j = li
            while j < len(line) and line[j] in " \t":
                j += 1

    def after_value(self, row: int, col: int, path: tuple) -> None:
        rest = self.lines[row][col:]
        s = rest.lstrip(" \t")
        if not s:
            return
        if s[0] == "#" and len(rest) > len(s):
            self.comments.after[path] = s.rstrip()
            return
        raise self.err("unexpected text after the value", row + 1, col + len(rest) - len(s) + 1,
                       "quote the whole value, or start a comment with ' #'")

    def block(self, k: int, c: int, parent: int, path: tuple) -> str:
        """A literal (|) or folded (>) block, with its chomping (- strips the final line breaks, + keeps them all) and
        an optional indentation digit."""
        line = self.lines[k]
        folded = line[c] == ">"
        j = c + 1
        chomp: Optional[str] = None
        explicit: Optional[int] = None
        for _ in range(2):
            if j < len(line) and line[j] in "+-" and chomp is None:
                chomp, j = line[j], j + 1
            elif j < len(line) and line[j] in "123456789" and explicit is None:
                explicit, j = int(line[j]), j + 1
        rest = line[j:]
        s = rest.lstrip(" \t")
        if s:
            if s[0] == "#" and len(rest) > len(s):
                self.comments.after[path] = s.rstrip()
            else:
                raise self.err(f"unexpected text after {line[c:j]}", k + 1, j + 1, f"the text of a {line[c]} block starts on the next line")
        width = parent + explicit if explicit else None
        body: list[Optional[str]] = []           # None for an empty line
        lead = 0
        row = k + 1
        while row < self.n:
            ln = self.lines[row]
            if ln.strip(" ") == "":
                if width is not None and len(ln) > width:
                    body.append(ln[width:])      # more spaces than the indentation: they are the line's text
                else:
                    body.append(None)
                    if width is None:
                        lead = max(lead, len(ln))
                row += 1
                continue
            li = len(ln) - len(ln.lstrip(" "))
            if width is None:
                if li <= parent:
                    break
                if lead > li:
                    raise self.err("a blank line at the start of the block has more spaces than its first line of text", k + 2, 1,
                                   "empty the blank line")
                width = li
            if li < width:
                break
            body.append(ln[width:])
            row += 1
        self.i = row
        return _block_text(body, folded, chomp)

    def flow(self, k: int, c: int, parent: int, path: tuple) -> Any:
        """A [ ] or { } collection, which may run over lines indented more than its key."""
        pieces: list[str] = []
        depth, quote = 0, None
        row, j = k, c
        start = j
        while True:
            line = self.lines[row]
            end = None
            while j < len(line):
                ch = line[j]
                if quote is not None:
                    if quote == '"' and ch == "\\":
                        j += 2
                        continue
                    if ch == quote:
                        if quote == "'" and line[j + 1:j + 2] == "'":
                            j += 2
                            continue
                        quote = None
                    j += 1
                    continue
                if ch in "\"'" and _value_starts(pieces, line, start, j):
                    quote = ch
                elif ch == "#" and j > start and line[j - 1] in " \t":
                    self.comments.before.setdefault(path, []).append(line[j:].rstrip())
                    break
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
                j += 1
            if end is not None:
                pieces.append(line[start:end])
                break
            pieces.append(line[start:j] if j <= len(line) else line[start:])
            row += 1
            while row < self.n and self.blank(row):
                pieces.append("")                # a blank line is a newline to a quoted value inside
                row += 1
            if row >= self.n:
                if quote is not None:
                    raise self.err("a quoted value inside this collection never ends", k + 1, c + 1, f"close it with {quote}")
                raise self.err(f"this {self.lines[k][c]} never closes", k + 1, c + 1, f"close it with {']' if self.lines[k][c] == '[' else '}'}")
            li = self.indent(row)
            if li <= parent:
                raise self.err("the lines of a [ ] or { } collection must be indented more than its key", row + 1, li + 1, "indent them")
            j = start = li
        value = _Flow("\n".join(pieces), lambda m, fix="": self.err(m, k + 1, c + 1, fix)).parse()
        self.i = row + 1
        self.after_value(row, end, path)
        return value


def _value_starts(pieces: list[str], line: str, start: int, j: int) -> bool:
    """Whether a quote at line[j] opens a quoted value: it follows [ { , or : (whitespace aside). Inside a plain
    value (it's, 2') a quote is only a character."""
    before = (line[start:j]).rstrip(" \t")
    if not before:
        k = len(pieces) - 1
        while k >= 0 and not pieces[k].rstrip(" \t\n"):
            k -= 1
        before = pieces[k].rstrip(" \t\n") if k >= 0 else ""
    return not before or before[-1] in "[{,:?"


def _quoted_end(text: str) -> int:
    """The index just after the quoted scalar that opens `text`, if it closes on this line; else -1."""
    q = text[0]
    j = 1
    while j < len(text):
        ch = text[j]
        if q == '"' and ch == "\\":
            j += 2
            continue
        if ch == q:
            if q == "'" and text[j + 1:j + 2] == "'":
                j += 2
                continue
            return j + 1
        j += 1
    return -1


def _quoted_line(text: str, err: Callable[[str, int], YAMLError]) -> tuple[str, int]:
    """(value, index after it) of the quoted scalar opening `text`, which must close on the line."""
    q = text[0]
    out: list[str] = []
    j = 1
    while j < len(text):
        ch = text[j]
        if ch == q:
            if q == "'" and text[j + 1:j + 2] == "'":
                out.append("'")
                j += 2
                continue
            return "".join(out), j + 1
        if ch == "\\" and q == '"':
            if j + 1 == len(text):
                break
            j, s = _escape(text, j, err)
            out.append(s)
            continue
        out.append(ch)
        j += 1
    raise err("a quoted key must close on its line", 0)


def _escape(line: str, j: int, err: Callable[[str, int], YAMLError]) -> tuple[int, str]:
    e = line[j + 1]
    if e in _ESCAPES:
        return j + 2, _ESCAPES[e]
    if e in _HEX:
        n = _HEX[e]
        h = line[j + 2:j + 2 + n]
        if len(h) != n or any(ch not in "0123456789abcdefABCDEF" for ch in h):
            raise err(f"\\{e} takes {n} hex digits", j)
        o = int(h, 16)
        if 0xD800 <= o <= 0xDFFF:
            raise err(f"\\{e}{h} is half of a surrogate pair, which YAML reads as a broken character: write the character "
                      "itself, or \\U and its 8 hex digits", j)
        if o > 0x10FFFF:
            raise err(f"\\{e}{h} is beyond Unicode", j)
        return j + 2 + n, chr(o)
    raise err(f"\\{e} is not an escape YAML knows", j)


def _block_text(body: list[Optional[str]], folded: bool, chomp: Optional[str]) -> str:
    """The text of a block's lines, folded or literal, chomped. The same algorithm as PyYAML's scanner."""
    n = len(body)
    k = 0
    while k < n and body[k] is None:
        k += 1
    breaks = ["\n"] * k
    chunks: list[str] = []
    line_break = ""
    while k < n:
        chunks.extend(breaks)
        text = body[k]
        assert text is not None
        leading_non_space = text[:1] not in (" ", "\t")
        chunks.append(text)
        line_break = "\n"
        k += 1
        count = 0
        while k < n and body[k] is None:
            count, k = count + 1, k + 1
        breaks = ["\n"] * count
        if k < n:
            nxt = body[k]
            assert nxt is not None
            if folded and leading_non_space and nxt[:1] not in (" ", "\t"):
                if not breaks:
                    chunks.append(" ")
            else:
                chunks.append(line_break)
    if chomp != "-":
        chunks.append(line_break)
    if chomp == "+":
        chunks.extend(breaks)
    return "".join(chunks)


class _Flow:
    """A [ ] or { } collection, as one string with its lines joined by newlines and its comments cut."""

    def __init__(self, s: str, err: Callable[..., YAMLError]) -> None:
        self.s, self.p, self.err, self.depth = s, 0, err, 0

    def parse(self) -> Any:
        v = self.value()
        self.ws()
        if self.p != len(self.s):
            raise self.err("unexpected text after the collection")
        return v

    def ws(self) -> None:
        while self.p < len(self.s) and self.s[self.p] in " \t\n":
            self.p += 1

    def peek(self) -> str:
        return self.s[self.p] if self.p < len(self.s) else ""

    def value(self) -> Any:
        self.ws()
        ch = self.peek()
        if ch == "[":
            return self.seq()
        if ch == "{":
            return self.map()
        if ch in "\"'":
            return self.quoted()
        if ch in "&*!":
            raise self.err({"&": "anchors", "*": "aliases", "!": "tags"}[ch] + " are not part of Rabbit Brain YAML")
        if ch == "" or ch in "#@`%,]}|>":
            raise self.err("a value is missing inside the collection" if ch in ",]}" or not ch else f"a value cannot start with {ch!r} unquoted",
                           "write null for an empty value" if ch in ",]}" else "quote it")
        return self.plain()

    def nest(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.err(f"nested more than {MAX_DEPTH} levels deep")

    def seq(self) -> list:
        self.nest()
        self.p += 1
        out: list = []
        while True:
            self.ws()
            if self.peek() == "]":
                self.p += 1
                self.depth -= 1
                return out
            out.append(self.value())
            self.ws()
            ch = self.peek()
            if ch == ",":
                self.p += 1
            elif ch == "]":
                continue
            elif ch == ":":
                raise self.err("a key: value pair inside [ ] is not part of Rabbit Brain YAML", "write a { } mapping inside the list")
            else:
                raise self.err("expected ',' or ']'")

    def map(self) -> dict:
        self.nest()
        self.p += 1
        out: dict = {}
        while True:
            self.ws()
            if self.peek() == "}":
                self.p += 1
                self.depth -= 1
                return out
            ch = self.peek()
            if ch in "\"'":
                key = self.quoted()
            elif ch in "[{":
                raise self.err("a key must be text")
            else:
                key = self.plain(key=True)
            if not isinstance(key, str):
                raise self.err(f"a key is text, and {key!r} is not", "quote it")
            self.ws()
            if self.peek() != ":":
                raise self.err(f"the key {key!r} needs ': ' and a value", "write {key: value}")
            self.p += 1
            if key in out:
                raise self.err(f"the key {key!r} appears twice", "keep one")
            self.ws()
            out[key] = None if self.peek() in (",", "}") else self.value()
            self.ws()
            ch = self.peek()
            if ch == ",":
                self.p += 1
            elif ch != "}":
                raise self.err("expected ',' or '}'")

    def quoted(self) -> str:
        """A quoted value, folded over lines as YAML folds them: a line's trailing and the next line's leading
        whitespace go, one line break is a space, each blank line a newline, and \\ at the end of a line joins."""
        s, q = self.s, self.s[self.p]
        out: list[str] = []
        j = self.p + 1
        keep = 0
        while j < len(s):
            ch = s[j]
            if ch == q:
                if q == "'" and s[j + 1:j + 2] == "'":
                    out.append("'")
                    keep = len(out)
                    j += 2
                    continue
                self.p = j + 1
                return "".join(out)
            if ch == "\\" and q == '"' and s[j + 1:j + 2] == "\n":
                j += 2
                while j < len(s) and s[j] in " \t":
                    j += 1
                keep = len(out)
                continue
            if ch == "\\" and q == '"':
                j, piece = _escape(s, j, lambda m, c: self.err(m))
                out.append(piece)
                keep = len(out)
                continue
            if ch == "\n":
                del out[keep:]
                blanks = 0
                j += 1
                while True:
                    while j < len(s) and s[j] in " \t":
                        j += 1
                    if j < len(s) and s[j] == "\n":
                        blanks += 1
                        j += 1
                        continue
                    break
                out.append("\n" * blanks if blanks else " ")
                keep = len(out)
                continue
            out.append(ch)
            if ch not in " \t":
                keep = len(out)
            j += 1
        raise self.err("a quoted value never ends")

    def plain(self, key: bool = False) -> Any:
        s, start = self.s, self.p
        if s[start] == "?" and s[start + 1:start + 2] in ("", " ", "\t", "\n"):
            raise self.err("complex keys (? ) are not part of Rabbit Brain YAML", "write the key as text on one line: quote it")
        while self.p < len(s):
            ch = s[self.p]
            if ch in ",[]{}":
                break
            if ch == ":" and (self.p + 1 == len(s) or s[self.p + 1] in " \t\n,[]{}"):
                break
            self.p += 1
        raw = s[start:self.p].strip(" \t\n")
        if not raw:
            raise self.err("a value is missing inside the collection", "write null for an empty value")
        if raw[0] in "-?:" and (len(raw) == 1 or raw[1] in " \t\n"):
            raise self.err(f"{raw[0]!r} alone cannot start a value", f'quote it: "{raw}"')
        if "\n" in raw:
            lines = [x.strip(" \t") for x in raw.split("\n")]
            if any(not x for x in lines):
                raise self.err("a blank line inside a value in [ ] or { }", "keep the value on one line")
            return " ".join(lines)
        try:
            return _resolve(raw)
        except _Ambiguous as a:
            raise self.err(a.message, a.fix)


def _identify(cm: Comments, data: Any) -> Comments:
    """Comments were collected by position; name list items by id or name where the list allows it."""
    def conv(path: tuple) -> tuple:
        out = []
        node = data
        for step in path:
            if isinstance(step, int) and isinstance(node, list) and step < len(node):
                scheme = _item_scheme(node)
                out.append((scheme, node[step][scheme]) if scheme else step)
                node = node[step]
            elif isinstance(node, dict) and isinstance(step, str) and step in node:
                out.append(step)
                node = node[step]
            else:
                out.append(step)
                node = None
        return tuple(out)
    before: dict[tuple, list[str]] = {}
    for p, v in cm.before.items():
        before.setdefault(conv(p), []).extend(v)
    return Comments(before, {conv(p): v for p, v in cm.after.items()}, list(cm.footer))


# ---------------------------------------------------------------- writing


def _float(x: float) -> str:
    if not math.isfinite(x):
        raise ValueError("Rabbit Brain YAML has no infinity or nan; record null for a value that is not a number")
    r = repr(x)
    if "e" in r or "E" in r:
        m, _, e = r.lower().partition("e")
        if "." not in m:
            m += ".0"
        if e[0] not in "+-":
            e = "+" + e
        return f"{m}e{e}"
    return r if "." in r else r + ".0"


def _dq(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif _BAD.match(ch):
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _plain_ok(s: str) -> bool:
    """Whether text can be written unquoted and read back as exactly itself, by every parser."""
    if not s or s[0] in _INDICATORS or s[0] in " \t" or s[-1] in " \t:":
        return False
    if "\n" in s or "\r" in s or "\t" in s or ": " in s or " #" in s or _BAD.search(s):
        return False
    if s.startswith(("---", "...")) or _DATE_PREFIX.match(s):
        return False
    try:
        v = _resolve(s)
    except _Ambiguous:
        return False
    return isinstance(v, str)


def _block_ok(s: str) -> bool:
    """Whether multi-line text can be written as a | block and read back exactly."""
    body = s.rstrip("\n")
    if "\n" not in s or not body.strip("\n"):
        return False
    lines = body.split("\n")
    first = next(x for x in lines if x)
    if first[0] in " \t":
        return False                              # it would need an indentation digit; quoting is plainer
    for x in lines:
        if x != x.rstrip(" \t") or "\t" in x or "\r" in x or _BAD.search(x):
            return False
    return True


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _float(v)
    if isinstance(v, str):
        return v if _plain_ok(v) else _dq(v)
    raise TypeError(f"Rabbit Brain YAML holds text, numbers, true/false, null, lists and mappings, not {type(v).__name__}")


def _key(k: Any) -> str:
    if not isinstance(k, str):
        raise TypeError(f"a key in Rabbit Brain YAML is text, not {type(k).__name__} ({k!r})")
    return k if _plain_ok(k) else _dq(k)


class _Emitter:
    def __init__(self, cm: Comments) -> None:
        self.cm, self.out, self.used = cm, [], set()

    def before(self, path: tuple, ind: int) -> None:
        cs = self.cm.before.get(path)
        if cs:
            self.used.add(path)
            for c in cs:
                if c:
                    self.out.append(" " * ind + c)
                elif self.out and self.out[-1] != "":
                    self.out.append("")

    def after(self, path: tuple) -> str:
        c = self.cm.after.get(path)
        if c:
            self.used.add(path)
            return " " + c
        return ""

    def value_lines(self, head: str, v: Any, ind: int, path: tuple, after: str) -> None:
        """`head` is `key:` or `-`; the value goes after it, or below it at ind + 2."""
        if isinstance(v, dict):
            if not v:
                self.out.append(f"{head} {{}}{after}")
            else:
                self.out.append(f"{head}{after}")
                self.map(v, ind + 2, path)
        elif isinstance(v, list):
            if not v:
                self.out.append(f"{head} []{after}")
            else:
                self.out.append(f"{head}{after}")
                self.seq(v, ind + 2, path)
        elif isinstance(v, str) and "\n" in v and _block_ok(v):
            trailing = len(v) - len(v.rstrip("\n"))
            self.out.append(f"{head} |{'-' if trailing == 0 else ('' if trailing == 1 else '+')}{after}")
            pad = " " * (ind + 2)
            for x in v.rstrip("\n").split("\n") + [""] * max(0, trailing - 1):
                self.out.append(pad + x if x else "")
        else:
            self.out.append(f"{head} {_scalar(v)}{after}")

    def map(self, d: dict, ind: int, path: tuple) -> None:
        sp = " " * ind
        for k, v in d.items():
            p = path + (k,)
            self.before(p, ind)
            self.value_lines(f"{sp}{_key(k)}:", v, ind, p, self.after(p))

    def seq(self, items: list, ind: int, path: tuple) -> None:
        sp = " " * ind
        scheme = _item_scheme(items)
        for idx, v in enumerate(items):
            p = path + (((scheme, v[scheme]) if scheme else idx),)
            self.before(p, ind)
            after = self.after(p)
            inline = not after and ((isinstance(v, dict) and v and not self.cm.before.get(p + (next(iter(v)),)))
                                    or (isinstance(v, list) and v and not self.cm.before.get(p + (_first_path(v),))))
            if inline:
                mark = len(self.out)
                if isinstance(v, dict):
                    self.map(v, ind + 2, p)
                else:
                    self.seq(v, ind + 2, p)
                self.out[mark] = sp + "- " + self.out[mark][ind + 2:]
            else:
                self.value_lines(f"{sp}-", v, ind, p, after)


def _first_path(items: list) -> Any:
    scheme = _item_scheme(items)
    return (scheme, items[0][scheme]) if scheme else 0


# ---------------------------------------------------------------- the API


def load(text: str, source: str = "") -> Document:
    """Parse Rabbit Brain YAML: the data, and the comments anchored to it. YAMLError says where and what to write instead."""
    p = _Parser(text, source)
    data = p.document()
    return Document(data, p.comments)


def loads(text: str, source: str = "") -> Any:
    return load(text, source).data


def dumps(data: Any, comments: Optional[Comments] = None) -> str:
    """The canonical Rabbit Brain YAML of `data`, with `comments` put back where their keys and items still are; a comment
    whose key is gone is kept at the end rather than dropped."""
    e = _Emitter(comments or Comments())
    if isinstance(data, dict) and data:
        e.map(data, 0, ())
    elif isinstance(data, list) and data:
        e.seq(data, 0, ())
    elif isinstance(data, dict):
        e.out.append("{}")
    elif isinstance(data, list):
        e.out.append("[]")
    elif isinstance(data, str) and "\n" in data and _block_ok(data):
        e.value_lines("", data, -2, (), "")
        e.out[0] = e.out[0].lstrip()
    else:
        e.out.append(_scalar(data))
    cm = comments or Comments()
    orphans = [c for p, cs in cm.before.items() if p not in e.used for c in cs if c]
    orphans += [c for p, c in cm.after.items() if p not in e.used]
    tail = orphans + list(cm.footer)
    if tail:
        e.out.extend(tail)
    while e.out and e.out[0] == "":
        e.out.pop(0)
    return "\n".join(e.out) + "\n"


def fmt(text: str, source: str = "") -> str:
    """The canonical form of an Rabbit Brain YAML text, its comments kept."""
    d = load(text, source)
    return dumps(d.data, d.comments)


def canonical_json(data: Any) -> str:
    """The machine form of the data: keys sorted, no whitespace, no NaN. What every content hash is taken of."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_sha(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
