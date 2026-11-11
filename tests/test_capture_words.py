"""EVIDENCE.md's words: one table, rust/src/shell/strings.rs's EVIDENCE_WORDS, that every surface draws from.

Read here as a table: each entry is drawn somewhere in rb's code (no word kept that no surface says), each `{name}` is
filled where it is drawn, and none breaks the rules for rb's own words. test_capture_failures.py checks each state's
sentence on the terminal, data.warnings, rb show, a page and rb journal run.
"""
import re

from rbtest import ROOT

RUST = ROOT / "rust" / "src"
TABLE = (RUST / "shell" / "strings.rs").read_text(encoding="utf-8")


def words() -> dict[str, str]:
    body = TABLE[TABLE.index("pub const EVIDENCE_WORDS"):]
    body = body[:body.index("];")]
    found = dict(re.findall(r'\("([a-z0-9_]+)",\s*"((?:[^"\\]|\\.)*)"\)', body))
    assert len(found) > 30, found
    return found


def code() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in RUST.rglob("*.rs"))


def test_every_entry_is_drawn_by_a_surface():
    src = code().replace(TABLE[TABLE.index("pub const EVIDENCE_WORDS"):], "")
    for key in words():
        assert re.search(rf'evidence\(\s*"{key}"', src), f"EVIDENCE_WORDS {key!r} is said nowhere"


def test_every_name_in_an_entry_is_filled_where_it_is_drawn():
    src = code()
    for key, text in words().items():
        for name in re.findall(r"\{([a-z_]+)\}", text):
            calls = re.findall(rf'evidence\(\s*"{key}",\s*&\[(.*?)\]\)', src, flags=re.S)
            assert calls and all(f'"{name}"' in c for c in calls), (key, name, calls)


def test_the_words_keep_rb_s_rules():
    for key, text in words().items():
        assert "—" not in text, key
        low = text.lower()
        for banned in ("verified", "established", "supported", "contradicted", "claim", "the person", "a person", "agent", "session"):
            assert banned not in low, (key, banned)


def test_the_code_s_leads_are_the_table_s_words():
    """review5 L5: rb show's and rb run add's "The code when it was recorded, ..." leads are EVIDENCE_WORDS entries,
    never a sentence of their own outside the table."""
    src = code().replace(TABLE[TABLE.index("pub const EVIDENCE_WORDS"):], "")
    found = words()
    for key in ("code_recorded", "code_recorded_earlier"):
        assert found[key].startswith("The code when it was recorded, ")
    assert "The code when it was recorded" not in src
