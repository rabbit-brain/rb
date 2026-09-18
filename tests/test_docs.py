"""AGENTS.md and the CLI must agree by construction: every command the doc names exists, every error code it lists is real, and nothing real is undocumented."""
import re
from pathlib import Path

from rabbit_brain.cli import COMMANDS, PLANNED, build_parser, docs_text
from rabbit_brain.errors import ERRORS

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def registered_commands() -> set[str]:
    parser = build_parser()
    names = set()
    for action in parser._subparsers._group_actions:
        for name, sub in action.choices.items():
            names.add(name)
            for a in getattr(sub, "_subparsers", None)._group_actions if getattr(sub, "_subparsers", None) else []:
                for subname in a.choices:
                    names.add(f"{name} {subname}")
    return names


def test_every_command_in_the_doc_exists():
    mentioned = set(re.findall(r"`rb ([a-z-]+(?: (?:save|run|list|rm))?)", DOC))
    mentioned |= set(re.findall(r"^rb ([a-z-]+(?: (?:save|run|list|rm))?)", DOC, flags=re.M))
    real = registered_commands()
    for m in mentioned:
        base = m.split(" ")[0]
        assert m in real or base in PLANNED or m in {"check list|rm", "check list", "check rm"}, f"AGENTS.md mentions `rb {m}` which does not exist"


def test_every_real_command_is_documented():
    for c in COMMANDS:
        assert re.search(rf"rb {re.escape(c)}\b", DOC) or (c in ("check list", "check rm") and "check list|rm" in DOC), f"{c} is not in AGENTS.md"


def test_error_codes_agree():
    in_doc = set(re.findall(r"`(E_[A-Z_]+)`", DOC))
    assert in_doc == set(ERRORS), f"doc/code mismatch: doc-only {in_doc - set(ERRORS)}, code-only {set(ERRORS) - in_doc}"


def test_rb_docs_prints_the_same_file():
    assert docs_text() == DOC


def test_doc_states_the_rule_agents_must_follow():
    assert "Do not compute errors, regressions, rankings, stability or verdicts yourself" in DOC
    assert "For humans: verify what your agent did" in DOC
