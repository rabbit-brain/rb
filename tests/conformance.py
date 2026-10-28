"""The conformance gate: a line rb composes around a record's own fields is held to a budget of words.

Every string a surface adds is a stored field verbatim, a computed number with its unit, or a word from the one
shipped vocabulary: the kinds `rb schema ir` names, the axis words a finding is read in, the one phrase each integrity
problem has, and the few frame words listed here. What a human or an agent wrote may say anything; the frame around
it may not. The budget grows reluctantly, reviewed like frozen copy: a new word on a surface fails here until it is
added on purpose."""
from __future__ import annotations

# An integrity problem, each code as its one short phrase, as every surface says it.
REASONS = {
    "unknown": "unknown settings",
    "conflict": "a source contradicts a setting",
    "stale": "a source changed",
    "confound": "an undeclared difference",
    "drift": "the frozen spec changed",
    "edited_outside_rb": "edited outside rb",
    "shallow_clone": "the clone is shallow",
    "cited_unchecked": "the cited number was not checked",
    "cited_source_changed": "the cited source changed",
    "inherited_without_parent": "inherited without a parent",
    "rejected_setting": "a setting someone rejected",
    "not_comparable": "settings differ from the source's",
    "state_newer": "part of the record needs a newer rb",
    "experiment_retracted": "its experiment was retracted",
    "retracted": "it was retracted",
    "analysis_source_changed": "the document stating it changed",
    "input_retracted": "what it read was retracted",
    "input_superseded": "what it read was superseded",
    "arms": "its arms are ambiguous",
    "units": "more units than the runs it read form",
}

# The frame words a surface may add around fields and numbers.
FRAME_WORDS = {"a", "agent", "and", "better", "by", "call", "counts", "criterion", "for", "frozen", "landed",
    "margin", "more", "no", "noise", "none", "not", "of", "on", "one", "planned", "population", "reading",
    "record", "rule", "run", "runs", "still", "superseded", "than", "the", "unit", "units", "varies", "vs", "within",
    "worse", "yet", "your"}

# The axis words a finding is read in, the only standing vocabulary there is.
AXIS_WORDS = {"after", "an", "before", "bound", "clear", "data", "decision", "declared", "difference", "earlier",
    "equivalent", "estimate", "evidence", "existed", "from", "gain", "gate", "gives", "higher", "history", "includes",
    "integrity", "interval", "is", "it", "loss", "lower", "mean", "measured", "meets", "met", "missed", "misses",
    "ok", "only", "order", "paired", "point", "rb", "rb's", "reproduced", "retracted", "seed", "seeds", "seen",
    "side", "t", "undetermined", "unknown", "unpaired", "unresolved", "untested", "upper", "welch"}

ALLOWED_PUNCT = '·=≤≥±/():.,%+−\'"<>'


def conforms(text: str, stored: list, kinds: list) -> bool:
    """Whether a composed line stays inside the budget: every word is from a stored field, a number, one of the
    record's kinds (`kinds`, as rb schema ir names them), an axis word, a REASONS phrase, or FRAME_WORDS."""
    vocab = set(FRAME_WORDS) | set(kinds) | set(AXIS_WORDS) | {"inherited", "comparable", "settings"}
    for phrase in REASONS.values():
        vocab.update(phrase.lower().split())
    for s in stored:
        vocab.update(s.lower().split())
    for token in text.replace("·", " ").split():
        t = token.strip(ALLOWED_PUNCT).lower()
        if not t or any(c.isdigit() for c in t):
            continue
        if t in vocab or any(t in s.lower() for s in stored):
            continue
        return False
    return True
