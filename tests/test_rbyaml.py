"""Rabbit Brain YAML: what it reads, what it refuses, what it writes, and that every other YAML parser agrees with it."""
from __future__ import annotations

import math
import random

import pytest

from rbtest import rbyaml as Y

yaml = pytest.importorskip("yaml")


def same(a, b) -> bool:
    """Equal and of the same types all the way down: 1 is not 1.0 is not True, and key order counts."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a) == list(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, float):
        return a == b and math.copysign(1, a) == math.copysign(1, b)
    return a == b


# ---------------------------------------------------------------- documents every parser reads the same way

ACCEPTED = [
    "a: 1\nb: 2.5\nc: -3\nd: true\ne: false\nf: null\ng: ~\nh:\ni: text\n",
    "a: 0\nb: +5\nc: -0\nd: 1.\ne: .5\nf: 1.5e+10\ng: 01.5\nh: 1.0e-05\ni: -2.25\n",
    "list:\n- a\n- b\nnext: 1\n",
    "list:\n  - a\n  - b\n",
    "- a\n- b: 1\n  c: 2\n- - x\n  - w\n-\n  d: 3\n- []\n- {}\n",
    "items:\n  - id: one\n    note: first\n  - id: two\n    tags:\n      - x\n      - z\n",
    "lit: |\n  line one\n  line two\nnext: 1\n",
    "strip: |-\n  a\n  b\n\nkeep: |+\n  a\n\n\nclip: |\n  a\n\n\nend: 1\n",
    "fold: >\n  one\n  two\n\n  three\n    indented\n  four\n",
    "fold_strip: >-\n  a b\n  c\n",
    "explicit: |2\n    two spaces kept\n  back\n",
    "empty_block: |\nnext: 1\n",
    'dq: "a \\"quoted\\" \\\\ backslash \\t tab \\n newline \\u00e9 \\x41 \\U0001F600"\n',
    "sq: 'it''s # not a comment'\n",
    'folded_dq: "one\n  two\n\n  three"\n',
    'escaped_break: "one\\\n  two"\n',
    "folded_sq: 'one\n  two'\n",
    "flow: [a, 'b c', \"d\", 1, 2.5, true, null, [x, z], {k: v, m: 2}]\nempty: []\nemap: {}\n",
    "multi: [a,\n  b,\n  c]\n",
    'json: {"a": 1, "b": [1, 2]}\n',
    "# top comment\na: 1 # trailing\n# middle\nb:\n  # inner\n  c: 2\n# footer\n",
    "---\na: 1\n...\n",
    "--- # a comment\na: 1\n",
    '"quoted key": 1\n\'single key\': 2\n"true": 3\n"1": 4\n',
    "url: https://example.com/a:b?c=d#frag\ntime: '10:30'\ndate: \"2026-09-30\"\n",
    "plain: this is a long\n  wrapped value\n\n  with a paragraph\nnext: 1\n",
    "unicode: é 中文 😀 ≤ ±\nnbsp: a\u00a0b\n",
    "a: 1\r\nb: 2\r\n",
    "\ufeffa: 1\n",
    "",
    "# only a comment\n",
    "key with spaces: 1\nkey.with.dots: 2\nkey/slash: 3\nkey-dash: 4\n",
    "a:b: 1\n",
    "s: a:b\nt: a :b\nu: '# hash'\nv: a#b\n",
    "n1: 12345678901234567890\n",
    "neg: [-1, -0.5, -1.5e+3]\n",
    "nested:\n  deeper:\n    deepest:\n      - 1\n      - 2\n",
    "quoted_multiline_key_value:\n  \"text\"\n",
    "item:\n  - |\n    block in a list\n  - >-\n    folded in\n    a list\n",
    "k: ['one\n    two', \"three\\\n    four\", 'five\n\n    ']\n",
    "-a: 1\n?b: 2\n:c: 3\n",
    "k: [it's, 2', \"x\"]\n",
]


@pytest.mark.parametrize("doc", ACCEPTED)
def test_reads_like_pyyaml(doc):
    ours = Y.loads(doc)
    theirs = yaml.safe_load(doc)
    assert same(ours, theirs), (ours, theirs)


@pytest.mark.parametrize("doc", ACCEPTED)
def test_canonical_form_round_trips_everywhere(doc):
    data = Y.loads(doc)
    out = Y.dumps(data)
    assert same(Y.loads(out), data)
    assert same(yaml.safe_load(out), data)
    assert Y.fmt(out) == out, "fmt is idempotent"
    kept = Y.fmt(doc)
    assert Y.fmt(kept) == kept, "fmt keeps comments and is idempotent"
    assert same(Y.loads(kept), data)


# ---------------------------------------------------------------- what Rabbit Brain YAML refuses, and says what to write

REFUSED = [
    ("a: &x 1\n", "anchors"),
    ("a: 1\nb: *x\n", "aliases"),
    ("a: !!str 1\n", "tags"),
    ("a: !custom x\n", "tags"),
    ("a: yes\n", "true or false"),
    ("a: No\n", "true or false"),
    ("a: on\n", "true or false"),
    ("a: y\n", "true or false"),
    ("a: 2026-09-30\n", "date"),
    ("a: 2026-09-30T10:00:00Z\n", "date"),
    ("a: 1e3\n", "number"),
    ("a: 1.5e10\n", "number"),
    ("a: 007\n", "number"),
    ("a: 08\n", "number"),
    ("a: 1_000\n", "number"),
    ("a: 0x1F\n", "number"),
    ("a: 0o17\n", "number"),
    ("a: .inf\n", "number"),
    ("a: .nan\n", "number"),
    ("a: 1.0e+999\n", "too large to keep as a number"),
    ("a: -1.0e+999\n", "too large to keep as a number"),
    ("a: 10:30\n", "number"),
    ("a: -.5\n", "number"),
    ("<<: {a: 1}\n", "special meaning"),
    ("a: 1\na: 2\n", "appears twice"),
    ("a: 1\n---\nb: 2\n", "one document"),
    ("%YAML 1.2\n---\na: 1\n", "directives"),
    ("? a\n: b\n", "complex keys"),
    ("a:\n\tb: 1\n", "tab"),
    ("a: b: c\n", "': '"),
    ("a: [1, 2\n", "never closes"),
    ('a: "\\q"\n', "escape"),
    ('a: "\\ud83d"\n', "surrogate"),
    ("a: \x01\n", "U+0001"),
    ("a: x\u2028y\n", "U+2028"),
    ("a: [b: 1]\n", "pair inside"),
    ("a: {b, c}\n", "needs ': '"),
    ("a: 1\n  b: 2\n", "indented more"),
    ("a: |\n  x\n y\n", "indented more"),
    ('a: "open\n', "never ends"),
    ("a: - b\n", "list cannot start"),
    ("a: @x\n", "cannot start with '@'"),
    ("a: `x\n", "cannot start with '`'"),
    ("true: 1\n", "a key is text"),
    ("1: x\n", "a key is text"),
    ("- a\nb: 1\n", "does not fit"),
    ('a: "x" y\n', "unexpected text"),
    ("a: |x\n  b\n", "unexpected text"),
]


@pytest.mark.parametrize("doc,words", REFUSED)
def test_refuses_what_parsers_disagree_on(doc, words):
    with pytest.raises(Y.YAMLError) as e:
        Y.loads(doc, "t.yaml")
    assert words in str(e.value), str(e.value)
    assert e.value.line >= 1 and str(e.value).startswith("t.yaml:")


def test_errors_carry_the_fix():
    with pytest.raises(Y.YAMLError) as e:
        Y.loads("created_at: 2026-09-30\n")
    assert e.value.fix == 'quote it: "2026-09-30"'
    assert (e.value.line, e.value.col) == (1, 13)


# ---------------------------------------------------------------- writing: whatever the data, every parser reads it back


NASTY = ["", " ", "a", "a b", " lead", "trail ", "null", "Null", "~", "true", "False", "yes", "no", "on", "off", "y", "n",
         "1", "-1", "+1", "1.0", "1.", ".5", "-.5", "1e3", "1.5e+3", "0x1f", "0o17", "007", "1_000", ".inf", ".nan",
         "2026-09-30", "2026-09-30 notes", "10:30", "1:2:3", "-", "- a", "-a", "?", "? a", "?a", ":", ": a", ":a", "a:",
         "a: b", "a:b", "#", "# x", "a #b", "a#b", "&x", "*x", "!x", "|", ">", "'", '"', "%", "@", "`", ",", "[", "]",
         "{", "}", "[a]", "{a: 1}", "---", "...", "--- x", "=", "<<", "é", "中文", "😀", "\u00a0x", "x\u00a0", "tab\there",
         "line\nbreak", "two\n", "three\n\n", "\nlead", "\n\n", "\n", "a\n b", "a \nb", "\\", "\\n", "C:\\path",
         "a\rb", "\x85", "\u2028", "\ufeff", "\x7f", "\x00", "\x1b[0m", "a\x0bb", "https://x.y/a:b#c", "val/loss",
         "change.val_loss", "population.images", "INT8 costs at most 0.05 nats", "≤ 0.05", "mean val_loss ≤ 5.7 (n ≥ 3)"]


def _rand_str(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return rng.choice(NASTY)
    alphabet = "ab :#-?'\"\\\n\t{}[],&*!|>%@`.0123456789eéx中😀 \u00a0"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))


def _rand_scalar(rng: random.Random):
    r = rng.random()
    if r < 0.4:
        return _rand_str(rng)
    if r < 0.55:
        return rng.choice([0, 1, -1, 7, 2 ** 40, -(2 ** 70), 10 ** 20])
    if r < 0.75:
        return rng.choice([0.0, -0.0, 0.1, 1 / 3, 5.64, 1e-5, 1.5e20, 5e-324, 1.7976931348623157e308, -2.5, 100.0, 1e16])
    if r < 0.9:
        return rng.choice([True, False])
    return None


def _rand_data(rng: random.Random, depth: int = 0):
    r = rng.random()
    if depth > 3 or r < 0.35:
        return _rand_scalar(rng)
    if r < 0.65:
        return [_rand_data(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    out = {}
    for _ in range(rng.randint(0, 4)):
        out[_rand_str(rng) if rng.random() < 0.5 else rng.choice(["id", "name", "note", "a", "b", "metrics", "x y"])] = _rand_data(rng, depth + 1)
    return out


def test_dumps_reads_back_exactly_everywhere():
    rng = random.Random(20260930)
    for _ in range(3000):
        data = {"rb": 1, "body": _rand_data(rng)}
        out = Y.dumps(data)
        assert same(Y.loads(out), data), out
        assert same(yaml.safe_load(out), data), out
        assert Y.fmt(out) == out


BY_DESIGN = ("complex keys", "number to some", "true or false", "U+2028", "U+0085", "U+FEFF")


def test_reads_whatever_pyyaml_writes_the_same_or_refuses_it_by_design():
    """PyYAML's own output in every style it has (block, flow, mixed; wrapped narrow or not; escaped or raw Unicode):
    Rabbit Brain YAML reads it exactly as PyYAML does, or refuses it for a reason Rabbit Brain YAML states (a complex key, a value 1.1 and
    1.2 parsers disagree on). Never a silent difference."""
    rng = random.Random(7)
    for _ in range(300):
        data = {"rb": 1, "body": _rand_data(rng)}
        for width in (20, 10 ** 6):
            for flow in (False, None, True):
                text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=width, default_flow_style=flow)
                if not same(yaml.safe_load(text), data):
                    continue
                try:
                    ours = Y.loads(text)
                except Y.YAMLError as e:
                    assert any(w in e.message for w in BY_DESIGN), (text, str(e))
                    continue
                assert same(ours, data), text


def test_nasty_strings_as_values_and_keys():
    for s in NASTY:
        for data in ({"v": s}, {s: 1}, [s], {"l": [s, {"k": s}]}):
            out = Y.dumps(data)
            assert same(Y.loads(out), data), (s, out)
            assert same(yaml.safe_load(out), data), (s, out)


def test_floats_are_floats_to_every_parser():
    for x in (1e-5, 1.5e20, 5e-324, 1e16, 123456789.123, 0.1 + 0.2, -0.0):
        text = Y.dumps({"x": x})
        assert "e" not in text or "e-" in text or "e+" in text
        assert same(yaml.safe_load(text)["x"], x) and same(Y.loads(text)["x"], x)
    with pytest.raises(ValueError):
        Y.dumps({"x": float("nan")})


def test_canonical_style():
    text = Y.dumps({"rb": 1, "kind": "run", "note": "two\nlines", "tags": [], "empty": {}, "when": "2026-09-30T10:00:00Z",
                    "items": [{"id": "a", "n": 1}], "flag": True, "none": None})
    assert text == ('rb: 1\nkind: run\nnote: |-\n  two\n  lines\ntags: []\nempty: {}\nwhen: "2026-09-30T10:00:00Z"\n'
                    'items:\n  - id: a\n    "n": 1\nflag: true\nnone: null\n')


# ---------------------------------------------------------------- comments are the reader's, and survive rewrites


def test_comments_survive_a_rewrite_with_new_data():
    doc = ("# why this experiment exists\nrb: 1\ntitle: int8 # the headline\nnotes:\n  # the first note\n  - text: a\n"
           "variants:\n  - name: fp32\n    role: baseline\n  # the candidate we ship\n  - name: int8\n    role: candidate\n# the end\n")
    d = Y.load(doc)
    d.data["notes"].append({"text": "b"})
    d.data["variants"].insert(0, {"name": "fp16", "role": "ablation"})
    d.data["title"] = "int8, renamed"
    out = Y.dumps(d.data, d.comments)
    assert out.startswith("# why this experiment exists\nrb: 1\n")
    assert 'title: int8, renamed # the headline' in out
    assert "  # the first note\n  - text: a\n  - text: b\n" in out
    assert "  # the candidate we ship\n  - name: int8\n" in out, "items keep their comments by name, not position"
    assert out.endswith("# the end\n")


def test_a_comment_whose_key_is_gone_is_kept_at_the_end():
    d = Y.load("a: 1 # about a\nb: 2\n")
    del d.data["a"]
    assert Y.dumps(d.data, d.comments) == "b: 2\n# about a\n"


def test_blank_lines_group_and_collapse():
    assert Y.fmt("\n\na: 1\n\n\n\nb: 2\n# c\n\n\n") == "a: 1\n\nb: 2\n# c\n"


def test_content_hash_ignores_comments_order_and_style():
    one = Y.loads("# note\nb: [1, 2]\na: 'x'\n")
    two = Y.loads("a: x\nb:\n  - 1\n  - 2\n")
    assert Y.content_sha(one) == Y.content_sha(two)
    assert Y.canonical_json(one) == '{"a":"x","b":[1,2]}'
    assert Y.content_sha(Y.loads("a: 1\n")) != Y.content_sha(Y.loads("a: 1.0\n"))
