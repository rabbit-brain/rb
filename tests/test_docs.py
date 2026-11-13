"""AGENTS.md, README.md and the CLI must agree by construction: every command the manual names exists, every error
code it lists is real, nothing real is undocumented, and every rb line in either file's shell blocks parses."""
import json
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from rabbit_brain.errors import ERRORS
from rabbit_brain.models import Envelope
from rbtest import as_the_user, cli_parser, main, registered_commands, ROOT

DOC = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
RETIRED = ("publish",)   # rb publish answers E_NOT_AVAILABLE and points to rb export


def answers_not_available(command: str, capsys) -> bool:
    """A command rb names without having: it answers E_NOT_AVAILABLE and says what to use."""
    capsys.readouterr()
    code = main([command, "--json"])
    env = Envelope.model_validate_json(capsys.readouterr().out)
    return code == 2 and [e.code for e in env.errors] == ["E_NOT_AVAILABLE"] and bool(env.errors[0].fix)


def test_every_command_in_the_doc_exists(capsys):
    mentioned = set(re.findall(r"`rb ([a-z-]+(?: run)?)", DOC))
    mentioned |= set(re.findall(r"^rb ([a-z-]+(?: run)?)", DOC, flags=re.M))
    real = registered_commands()
    for m in mentioned:
        base = m.split(" ")[0]
        assert m in real or answers_not_available(base, capsys), f"AGENTS.md mentions `rb {m}` which does not exist"


def test_every_real_command_is_documented():
    for c in cli_parser.COMMANDS:
        assert re.search(rf"rb {re.escape(c)}\b", DOC), f"{c} is not in AGENTS.md"
    assert {c.split(" ")[0] for c in cli_parser.COMMANDS} == {c for c in registered_commands() if " " not in c}


def test_retired_commands_are_named_only_as_retired(capsys):
    """rb publish is not rb's: the command still answers, with E_NOT_AVAILABLE and what to use, and the doc names it
    only where it says so."""
    assert all(answers_not_available(cmd, capsys) for cmd in RETIRED)
    for line in DOC.splitlines():
        for cmd in RETIRED:
            if f"rb {cmd}" in line:
                assert "E_NOT_AVAILABLE" in line, f"AGENTS.md names `rb {cmd}` outside the note that it is retired: {line[:120]}"
    assert "E_HUMAN_ONLY" not in DOC and ".rabbitbrain/" not in DOC


def shell_command_lines(text: str) -> list[str]:
    """Every rb command in the doc's ```sh blocks: continuation lines joined, `a && b` split, the trailing comment cut."""
    found = []
    for block in re.findall(r"```sh\n(.*?)```", text, flags=re.S):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            for part in line.split("&&"):
                part = re.sub(r"\s+#\s.*$", "", part).strip()
                if part.startswith("rb "):
                    found.append(part)
    return found


@pytest.mark.parametrize("name, at_least", [("AGENTS.md", 40), ("README.md", 10)], ids=["manual", "readme"])
def test_every_command_line_in_the_doc_parses(name, at_least):
    """Worked examples are copied and run as written: each rb line in a shell block of the manual or the README must
    be a command line rb accepts, with <placeholders> standing for a value and [--optional parts] left out. The
    README's work typed by hand (How it works) is such a block again since Nov 12."""
    lines = shell_command_lines((ROOT / name).read_text(encoding="utf-8"))
    assert len(lines) > at_least and any(line.startswith("rb run add ") for line in lines)
    parser = cli_parser.build_parser()
    for line in lines:
        argv = shlex.split(re.sub(r"<[^>]*>", "X", re.sub(r"\[[^\]]*\]", "", line)))[1:]
        try:
            parser.parse_args(argv)
        except cli_parser.UsageError as exc:
            raise AssertionError(f"{name} shows a command rb does not accept: {line}\n  {exc.message}") from None


def test_the_guides_declare_a_criterion_with_the_question_it_bears_on(workdir, monkeypatch, capsys):
    """A question's reading counts only the criteria linked to it, so rb's own guides link them: every criterion the
    manual and the README declare names its question, and the skill, llms.txt and the guide rb context opens with name
    -q and the filing that links a criterion already recorded."""
    for name in ("AGENTS.md", "README.md"):
        adds = [line for line in shell_command_lines((ROOT / name).read_text(encoding="utf-8")) if line.startswith("rb criterion add ")]
        assert adds and all(" -q " in line for line in adds), (name, adds)
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"^- `rb criterion add [^`]* -q <q> [^`]*`", skill, flags=re.M)
    assert re.search(r'^- `rb criterion file <criterion> <q> --why "\.\.\."`', skill, flags=re.M)
    assert "`rb criterion file <criterion> <question> --why" in (ROOT / "llms.txt").read_text(encoding="utf-8")
    as_the_user(monkeypatch)
    assert main(["init", "a guide", "--no-agents"]) == 0
    capsys.readouterr()
    assert main(["context"]) == 0
    guide = capsys.readouterr().out
    assert "-q <question> on rb criterion add" in guide and 'rb criterion file <criterion> <question> --why "..."' in guide


def test_error_codes_agree(capsys):
    """The doc, the table rb prints (rb docs --errors) and the one the SDK raises with are the same codes, each with
    the same meaning and fix."""
    in_doc = set(re.findall(r"`(E_[A-Z_]+)`", DOC))
    assert in_doc == set(ERRORS), f"doc/code mismatch: doc-only {in_doc - set(ERRORS)}, code-only {set(ERRORS) - in_doc}"
    assert main(["docs", "--errors", "--json"]) == 0
    said = Envelope.model_validate_json(capsys.readouterr().out).data["errors"]
    assert said == {code: {"meaning": meaning, "fix": fix} for code, (meaning, fix) in ERRORS.items()}


def test_rb_docs_prints_the_same_file(capsys):
    assert main(["docs", "--json"]) == 0
    assert Envelope.model_validate_json(capsys.readouterr().out).data["markdown"] == DOC
    assert main(["docs"]) == 0
    assert capsys.readouterr().out == DOC.rstrip("\n") + "\n"


def test_doc_states_the_rule_agents_must_follow():
    assert "Do not compute errors, regressions, rankings or findings yourself" in DOC
    assert "## Verify what your agent did" in DOC


# ---------------------------------------------------------------- the codes rb status raises, and the manual's lists

SRC = ROOT / "rust" / "src"
# the open items rb status raises: views.rs, and support.rs for what runs and texts rest on
VIEWS = "\n".join((SRC / "ledger" / name).read_text(encoding="utf-8") for name in ("views.rs", "support.rs"))


def match_arms(text: str, head: str) -> list:
    """(start, end, codes) of each arm of every `match <head> {` in text: where the arm's body is, and the codes its
    pattern names."""
    arms = []
    for m in re.finditer(re.escape(head) + r" \{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        heads = list(re.finditer(r'^[ \t]*((?:"[a-z_]+"\s*\|\s*)*"[a-z_]+"|_)\s*=>', text[m.end():i - 1], flags=re.M))
        for h, after in zip(heads, [*heads[1:], None]):
            arms.append((m.end() + h.start(), m.end() + (after.start() if after else i - 1 - m.end()), re.findall(r'"([a-z_]+)"', h.group(1))))
    return arms


def raised_item_codes() -> dict:
    """Every code a status item can carry, with who usually acts on it: the code an item() call in views.rs names, or,
    where it passes a finding's or a setting's code on, each code the match arm it sits in takes, or where it is built
    as one of several, each code it is built from."""
    [owner] = set(re.findall(r'let owner = "(\w+)";', VIEWS))
    arms = match_arms(VIEWS, "match p.code.as_str()") + match_arms(VIEWS, "match cav.code.as_str()")
    raised: dict = {}
    for call in re.finditer(r"\bitem\(\s*(?:&mut\s+)?items,\s*([^,]+?),\s*([^,]+?),", VIEWS):
        who, code = call.group(1).strip(), call.group(2).strip()
        who = owner if who == "owner" else who.strip('"')
        if code.startswith('"'):
            codes = [code.strip('"')]
        elif code in ("&p.code", "&cav.code"):
            [codes] = [named for start, end, named in arms if start <= call.start() < end]
        elif code == "code":
            built = VIEWS[VIEWS.rindex("let (code, what, command) =", 0, call.start()):call.start()]
            codes = re.findall(r'\(\s*"([a-z_]+)",\s*format!\(', built)
        else:
            raise AssertionError(f"views.rs raises an item whose code this test cannot read: {code}")
        assert codes, call.group(0)
        for c in codes:
            raised.setdefault(c, set()).add(who)
    return raised


def documented_item_codes() -> dict:
    """The manual's list of item codes by who usually acts: the user's, the agent's and anyone's."""
    [para] = [p for p in DOC.split("\n\n") if p.startswith("Every item in the two lists carries `who`")]
    groups = {"human": r"are the user's \(([^)]*)\)", "agent": r"are the agent's: (.*?)\. ", "anyone": r"are anyone's \(([^)]*)\)"}
    return {who: set(re.findall(r"`([a-z_]+)`", re.search(pattern, para).group(1))) for who, pattern in groups.items()}


def test_every_item_rb_status_can_raise_is_listed_in_the_manual_with_who_acts():
    """Each status item carries its exact command; the manual lists every code rb can raise under who usually acts on
    it, and nothing rb never raises."""
    raised = raised_item_codes()
    assert len(raised) > 40 and {"question_link_outdated", "question_link_unavailable", "finding_relation_invalid"} <= set(raised)
    assert all(len(who) == 1 for who in raised.values()), {c: who for c, who in raised.items() if len(who) > 1}
    documented = documented_item_codes()
    for who, listed in documented.items():
        codes = {c for c, acts in raised.items() if acts == {who}}
        assert listed == codes, f"{who}: listed and never raised {listed - codes}, raised and not listed {codes - listed}"


def raised_problem_codes() -> set:
    """Every integrity problem code rb's source can raise: a Caveat::problem's code, the codes of the match arm that
    passes one on, and a Caveat::new's code, a problem when its setting is required."""
    codes = set()
    for path in SRC.rglob("*.rs"):
        text = path.read_text(encoding="utf-8")
        codes |= set(re.findall(r'Caveat::problem\(\s*"([a-z_]+)"', text))
        for arm in re.findall(r'((?:"[a-z_]+"\s*\|\s*)*"[a-z_]+")\s*=>\s*Some\(Caveat::problem\(&\w+', text):
            codes |= set(re.findall(r'"([a-z_]+)"', arm))
        codes |= set(re.findall(r'Caveat::new\(\s*(?:if [^{]*\{\s*)?"([a-z_]+)"', text))
    return codes


def test_every_integrity_problem_rb_can_read_is_in_the_manual():
    """The manual's Integrity section names every problem code a finding, a question's reading or a relation can
    carry, and none rb never raises."""
    section = DOC[DOC.index("**Integrity.** Each problem carries a code"):DOC.index("The notes (`caveats`) are not problems")]
    heads = re.findall(r"^- (.*?): ", section, flags=re.M)
    [readings] = [p for p in section.split("\n\n") if p.startswith("A question's reading and a declared relation carry problems")]
    documented = set(re.findall(r"`([a-z_]+)`", " ".join([*heads, readings])))
    raised = raised_problem_codes()
    assert {"question_link_retracted", "question_link_ambiguous", "relation_outdated", "relation_scope_invalid", "relation_target_missing"} <= raised
    assert documented == raised, f"documented and never raised {documented - raised}, raised and not documented {raised - documented}"


def test_the_integrity_gate_says_it_fails_on_links_and_relations():
    """The integrity gate, and the CI gate that takes it in, fail on a question link or a declared relation rb cannot
    read as well as on a finding's problem: the gate's line and every line of the manual that says what the CI gate
    fails on say so (the README says nothing of CI since readme-2; the docs' Findings page does)."""
    [gate] = [line for line in DOC.splitlines() if line.startswith("- `integrity`: ")]
    assert "question link" in gate and "relation" in gate
    ci = [line for line in DOC.splitlines() if "the CI gate" in line]
    assert len(ci) >= 1 and all("question link" in line and "relation" in line for line in ci), ci


def test_rb_status_prints_its_parts_in_the_order_the_manual_gives(workdir, monkeypatch, capsys):
    """The manual's numbered list of what rb status prints, in order, against what it prints for a record that has a
    part for each."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    for argv in (["init", "Ordering", "--no-agents"], ["question", "add", "Does the order hold?", "--id", "q1"],
                 ["experiment", "add", "the order", "--id", "e1", "-q", "q1", "--baseline", "old", "--candidate", "new"],
                 ["experiment", "add", "nothing yet", "--id", "e2", "--baseline", "old", "--candidate", "new"],
                 ["metric", "add", "loss", "--minimize"],
                 ["criterion", "add", "new costs no loss", "-e", "e1", "--metric", "change.loss", "--at-most", "0", "-q", "q1", "--id", "c1"],
                 ["run", "add", "e1", "new.loss=2", "old.loss=1"],
                 ["analysis", "add", "e1", "--compare", "new/old:loss=1[0.5,1.5]", "--method", "bootstrap", "--id", "a1"],
                 ["note", "add", "a note on the project"],
                 ["resource", "add", "holdout", "--type", "dataset", "--name", "held-out set"]):
        assert main([*argv, "--json"]) == 0, argv
    capsys.readouterr()
    main(["status", "--full"])
    lines = capsys.readouterr().out.splitlines()
    parts = [("the project, and the last", lambda line: line.startswith("Ordering · ")),
             ("where each question stands", lambda line: line.startswith("Does the order hold? (")),
             ("the tally of the criteria", lambda line: line.startswith("Rules: ")),
             ("**Needs judgment**", lambda line: line.startswith("Needs judgment (")),
             ("**Can be done now**", lambda line: line.startswith("Can be done now (")),
             ("each criterion on its axes, with its integrity problems", lambda line: line == "Rules"),
             ("each experiment with its state", lambda line: line == "Experiments"),
             ("the runs, and whether", lambda line: line.startswith("Runs: ")),
             ("the analyses, and which", lambda line: line == "Analyses"),
             ("the notes on the project", lambda line: line == "Notes on the project"),
             ("each resource with its availability", lambda line: line == "Resources")]
    listed = DOC[DOC.index("`rb status --full [<exp>]` prints the long form, in order:"):DOC.index("**What changed since you last looked.**")]
    numbered = {int(n): text for n, text in re.findall(r"^(\d+)\. (.*)$", listed, flags=re.M)}
    in_manual = [next(n for n, text in numbered.items() if phrase in text) for phrase, _ in parts]
    printed = [next(i for i, line in enumerate(lines) if said(line)) for _, said in parts]
    assert in_manual == sorted(set(in_manual)), in_manual
    assert printed == sorted(set(printed)), printed


def test_the_manual_names_the_mcp_field_python_names_otherwise():
    """MCP's criterion_add takes `question` where Python's exp.criterion takes `questions`; the manual says so where it
    says the rest are the same."""
    import inspect
    import json
    from rabbit_brain import sdk
    tools = {t["name"]: t for t in json.loads((SRC / "assets" / "mcp.json").read_text(encoding="utf-8"))["tools"]}
    python = set(inspect.signature(sdk.Experiment.criterion).parameters)
    assert "question" in tools["criterion_add"]["props"] and "questions" in python and "question" not in python
    [said] = [line for line in DOC.splitlines() if "Over MCP these are `criterion_add`" in line]
    assert re.search(r"`criterion_add`, whose `question` takes the list Python calls `questions`", said)


def test_a_command_that_writes_is_never_described_as_a_read():
    """Each command that writes the record (the record's commands in the registry) says so in its help: none opens on
    reading, showing or listing."""
    registry = (SRC / "commands.rs").read_text(encoding="utf-8")
    record_commands = set(re.findall(r'"(cmd_\w+)" => r::\1', registry))
    helps, funcs = {}, {}
    for action in cli_parser.build_parser()._subparsers._group_actions:
        for choice in action._choices_actions:
            helps[choice.dest] = choice.help or ""
        for name, sub in action.choices.items():
            funcs[name] = sub.get_default("func")
            for group in sub._subparsers._group_actions if sub._subparsers else []:
                for choice in group._choices_actions:
                    helps[f"{name} {choice.dest}"] = choice.help or ""
                for word, leaf in group.choices.items():
                    funcs[f"{name} {word}"] = leaf.get_default("func")
    writes = [c for c, func in funcs.items() if func in record_commands]
    assert "criterion relate" in writes and "spec verify" in writes and len(writes) > 20
    for command in writes:
        assert not re.match(r"(read|show|list|print)\b", helps[command]), f"rb {command}: {helps[command]}"


def test_the_manual_names_the_words_the_writer_check_reads_as_words():
    """Review of group 9: the manual's list of what the writer check reads ran "agent, session, memory or UNSUPPORTED"
    into its sentence, and UNSUPPORTED read as a placeholder left in. The words are named as words."""
    assert "the words `agent`, `session`, `memory` or `UNSUPPORTED` where they speak of the record's own writing" in DOC


def test_the_manual_says_what_rb_cannot_promise_of_a_design_and_what_it_does():
    """Oct 13: the manual says in plain sentences what rb cannot promise of a method (a choice that only lived in the
    chat, whether a method fits the question) beside the design's standing. The README said it too until readme-2; the
    docs' The record page says it now (What rb does not do)."""
    assert "it cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question" in DOC
    # the snapshot rule (Oct 14): a gate a session wrote waits with its design until it reads as in one the user owns, in
    # a session's voice, the user's later branch or words beside it included (review of the snapshot rule, round 1)
    said = " ".join(DOC.split())
    assert "A gate waits with its design while a session's line wrote a branch or its words as they read now, until it reads as in a design the user owns" in said
    assert "with a branch or words the user gave beside them read as given; so does a why a session's line wrote as it reads now, as its note." in said
    assert "No line's time decides whose they are: words a session and the user both gave read as given." in said
    assert "even where the session put back the gate the user had" not in said
    # review of the snapshot rule, round 2: an earlier rb's key is not sure to name the design as it reads now
    assert "and so is a design's key an earlier rb listed, saying rb cannot tell from it whether the design changed since, each with the design's command." in said
    assert "puts back the gate the user had changes nothing" not in said


def test_the_manual_says_how_a_design_is_read_confirmed_and_reopened_in_short_parts():
    """Review of group E: the manual's paragraph on an experiment's design ran to some 550 words in one block, and said
    nothing of an adoption, an edit outside rb, an interpretation or the confirmation's line. It is a short paragraph
    and a list now, each item one point; the standing is the snapshot rule (Oct 14), said in a few plain sentences with
    the consequence that a design a session touched waits for one confirmation."""
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(text) if line.startswith("**An experiment's design.**"))
    section = [text[start]] + [line for line in text[start + 1:start + 10] if line.startswith("- ")]
    assert len(section) >= 6 and all(len(line.split()) <= 130 for line in section), [len(line.split()) for line in section]
    said = " ".join(section)
    for part in ("The confirmation is one log line, as a gate's is", "it reads confirmed only while it is exactly a design the user owns",
                 "So once a session touched a design it waits for one confirmation, even after the user rewrites every part the session wrote",
                 "a merge, a clone, an older rb or a clock can only make rb ask again", "an edit outside rb is a difference too",
                 "the interpretation a session declared with a criterion it listed", "`yours` where no session's line wrote any part of it"):
        assert part in said, part
    # review of the snapshot rule, round 1: a session's line names only what it changed, compared in rb's structured form
    assert "what the line says it changed (`detail.changed_parts`, by experiment, compared in rb's structured form" in said
    assert "so it owns the design only while no session's line it did not see wrote a part of it" in said
    # review of the snapshot rule, round 3: a confirmation that names no line it saw took them from where they fall in the log
    assert "as it stands; one that names no line it saw is taken to have seen none, and no change since it is said." in said
    # recheck of round 3: a file edited outside rb is said beside the parts that are, and so is a line that names none
    assert "an edit outside rb is a difference too, named by its file, as is a file written since where rb can name no part." in said
    assert "text escaped where a screen could hide it, every digit" in said
    assert "an adoption keeps one only where rb is sure what its files held before" in said
    assert "and what tells apart two values rb says alike: `was 3 (a number), now 3 (text)`" in said
    # every older line that writes none of a design's parts, as rb reads them (review of the snapshot rule, round 1)
    assert "but for a note or its retraction, a relation, a check of a source, a reindex or a restore." in said
    for gone in ("detail.seen", "an adoption counts only for the parts it changed", "An edit outside rb is `rb doctor`'s to list, not a change.", "changed or stated"):
        assert gone not in said, gone


def test_the_manual_says_a_found_file_in_the_top_folder_is_counted_in_both_lines():
    """Trial of group F: the manual said a file rb lists as found is left out of the changed files, but a file in the
    top folder is counted in both lines, as rb status says. It names where results land and says so."""
    said = " ".join(DOC.split())
    assert "and a file in a folder where results land that rb already lists as found;" in said
    assert "A file in the top folder that rb lists as found is counted in both lines, and `rb status` says how many (`1 also counted above`)." in said
    assert "a folder the walk reads that rb already lists as found" not in said


def test_the_manual_states_rb_s_limits():
    """Oct 14, decision 6: the manual's boundaries carry the Oct 13 line (what rb cannot promise of a method, and what it
    does). The README said where rb looks until readme-2; the docs' The record page says it (What rb sees)."""
    bounds = " ".join(DOC.split("## Boundaries", 1)[1].split("## ", 1)[0].split())
    for said in ("It cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question.",
                 "What it does: nothing a session wrote reads as the user's until they confirm it, every setting says where its value came from, "
                 "and every finding is computed from the files, not from the agent's account."):
        assert said in bounds, said


def test_the_security_policy_offers_a_private_contact_before_private_reporting_is_on():
    """Until the repository offers private vulnerability reporting, a report has a way in that discloses nothing: an
    issue that only asks for a private contact."""
    policy = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security/advisories/new" in policy
    assert "[open an issue](https://github.com/rabbit-brain/rb/issues/new) that only asks for a private contact, with no details" in policy


def test_the_manual_states_the_format_rule():
    """Owner decision 5 (Oct 18): format 1 is fixed, a new field means a new format and an older rb says to upgrade; the
    manual says so, and that a field rb does not know in a format-1 file is most likely a hand edit."""
    said = " ".join(DOC.split())
    assert "Format 1 is fixed: a new field means a new format, so a later rb that adds a field to a record writes that file in format 2, and an older rb says to upgrade" in said
    assert "A field rb does not know in a format-1 file is therefore most likely a hand edit" in said





def test_llms_txt_states_rb_s_limits():
    """llms.txt, which agents and crawlers read, says what rb cannot do, where it looks and what a receipt is (the
    README said the same until readme-2)."""
    llms = " ".join((ROOT / "llms.txt").read_text(encoding="utf-8").split())
    for said in ("It cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question.",
                 "It sees only what is in the repository and what runs through `rb journal run`: on its own it looks in `out/`, `outputs/`, "
                 "`results/`, `eval-out/`, `metrics/`, every folder named for results down to four levels (`reports/stage1/eval/`, `artifacts/`) "
                 "and any folder a recorded run named,",
                 "not proof of what produced it."):
        assert said in llms, said


def test_the_guides_say_a_miss_in_rb_s_words():
    """No guide says a result "contradicts" anything, but where it says never to: rb init copies the skill into users'
    AGENTS.md, and the start protocol asks for a miss in rb's words."""
    for name in ("AGENTS.md", "SKILL.md", "README.md", "llms.txt", "rust/src/assets/start.md"):
        text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        assert text.lower().count("contradict") == text.count('never "contradicts"'), name


def test_the_changelog_says_where_rb_looks_what_nothing_cites_and_names_a_place_by_branch_and_path():
    log = " ".join((ROOT / "CHANGELOG.md").read_text(encoding="utf-8").split())
    assert "**Where rb looks, said where it reads.**" in log and "**Changes nothing in the record cites.**" in log
    assert "names work elsewhere by its commit" not in log and "`rb status` names each place elsewhere by its branch and path, as rb names" in log
    assert "the owner's" not in log


def test_no_punctuation_touches_an_address_in_the_manual_or_the_readme():
    """Nov 7: a terminal's link took the full stop after the schema's address, and sent readers to a page that is not
    there. Every web address in AGENTS.md and README.md stands with nothing touching it: in angle brackets in a
    sentence, alone at the end of a link definition, between spaces in a command, or, in the README, as markup: in
    quotes as the value of an attribute of its picture (the <picture> block's srcset and src), or as the target of a
    Markdown link, `[Docs](https://...)`, which GitHub and PyPI render as a link to exactly that address. A
    placeholder (`https://...`) is no address. Since Nov 12 the quick start's prompt holds its addresses between
    spaces or at a line's end, so it is checked as every other line is."""
    for name in ("AGENTS.md", "README.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"https?://", line):
                end = m.start()
                opened = line[max(m.start() - 2, 0):m.start()]
                stops = '<>"' if opened.endswith('"') else "<>)" if opened == "](" else "<>"   # an attribute, a link target
                while end < len(line) and not line[end].isspace() and line[end] not in stops:
                    end += 1
                url, before, after = line[m.start():end], line[m.start() - 1:m.start()], line[end:end + 1]
                if url.startswith("https://...") or url.startswith("http://..."):
                    continue
                bracketed = before == "<" and after == ">"
                quoted = name == "README.md" and (before, after) in (('"', '"'), ("(", ")"))   # an attribute, a link target
                quoted &= (before == '"' or opened == "](") and url[-1] not in ".,;:)]!?'\"`"
                spaced = before in ("", " ") and after in ("", " ") and url[-1] not in ".,;:)]!?'\"`"
                assert bracketed or spaced or quoted, f"{name}:{n}: punctuation touches {url!r} (before it {before!r}, after it {after!r})"


def test_the_privacy_note_covers_rb_and_points_to_the_website_s():
    """Nov 7 (10:44Z): the website counts page views, so PRIVACY.md's "Analytics is off by default" is said of rb (the CLI,
    the SDK, MCP and its viewers), with the website's own note named apart; llms.txt keeps its line true the same way."""
    note = " ".join((ROOT / "PRIVACY.md").read_text(encoding="utf-8").split())
    llms = " ".join((ROOT / "llms.txt").read_text(encoding="utf-8").split())
    assert "This note covers `rb` itself: the CLI, the Python SDK, its MCP server and its viewers" in note
    assert "In `rb`, analytics is off by default" in note and "<https://rabbitbrain.ai/privacy>" in note
    assert "`rb`'s usage analytics is off unless" in llms and "<https://rabbitbrain.ai/privacy>" in llms


def test_the_manual_says_which_writes_beside_an_unreadable_file_succeed_and_which_are_refused():
    """MP-1107-2006 should 1: the sentence read as if every rb run add succeeds beside a file that does not read. It
    names the row path's --select, and the writes still refused (tests/test_format_two.py holds each)."""
    said = DOC[DOC.index("and any other write is made whole and succeeds"):DOC.index("`rb doctor` still runs.")]
    assert "`rb run add` of numbers typed or read from a file" in said and "the SDK's `record()` and `analysis()`" in said
    assert "where `--select` names them" in said and "every column of the row that holds a number is one" in said
    assert "refuses a batch of several whole" in said
    assert "`rb reconcile --apply`, which reads them to place what it found" in said and "beside an analysis's, `rb analysis add` on its experiment" in said
    assert "a question's or a resource's among them" in said
    # MP-1108-0010 shoulds 1 and 2: criterion add and freeze beside an analysis's file succeed; a note's file refuses
    # only a write to that note; experiment.yaml refuses every write to its experiment; a metric's file refuses
    # numbers that name no variant as a criterion's does
    assert "`rb criterion add`, `rb criterion relate` (a withdrawal with `--remove` too) and `rb freeze` beside an analysis's file" in said and "already recorded (nothing written)" in said
    assert "beside an experiment's note or a project note that does not read, every write leaves that note out" in said
    assert "only a write to that note itself (`rb retract <note>`) is refused" in said
    assert "beside an experiment's `experiment.yaml`, which declares its variants, every write to that experiment" in said
    assert "beside a criterion's or a metric's file, `rb reconcile --apply`" in said and "a run of numbers that name no variant" in said
    assert "a note's or an experiment's file, which it reads" not in said
    assert "`rb question add --parent` of it succeeds" in said and "a write that links that question" not in said
    # MP-1108-0425: criterion relate beside an analysis's file and a note's retraction beside a criterion's are made
    # whole; the refused list names question move, decide on a question, resource locate and criterion relate
    assert "`rb criterion add`, `rb criterion relate` (a withdrawal with `--remove` too) and `rb freeze` beside an analysis's file" in said
    assert "`rb retract` of a note beside a criterion's file" in said and "(a note's retraction aside)" in said
    assert "`rb question reword` and `rb question move` of it, and `rb decide` on it" in said
    assert "`rb resource expose` and `rb resource locate` of it" in said and "`rb criterion file`, `rb criterion relate`, `rb spec set`" in said
    assert "naming the note's file and where it breaks" in said


def test_the_manual_says_a_command_run_inside_another_tells_nothing_of_its_own():
    """Codex S4 should: the run add that rb reconcile --apply runs told the session's facts into a reply nobody read."""
    assert "A command rb runs inside another (the `rb run add` that `rb reconcile --apply` runs for each run it records) tells nothing of its own" in DOC
    assert "tells what it would tell were that note readable, and that the note's file changed outside rb" in DOC


def test_the_manual_says_two_rules_of_what_changed_apart():
    """MP-1108-0809 later 3: the manual joined "the enclosing command tells it" and a write beside a broken note with
    "and so does", which read as if that write told the inner command's facts. Each is its own sentence, and a read
    beside such a note is said too."""
    assert "the enclosing command tells it, and so does" not in DOC
    assert "the enclosing command tells it. A command beside a note's file that does not read" in DOC
    assert "a read that stops at that file fails, and tells nothing" in DOC


# ---------------------------------------------------------------- the README: plain, specific sentences, its structure kept

README = (ROOT / "README.md").read_text(encoding="utf-8")
# Nov 12, the owner: every public text cut to plain, specific sentences with its structure kept. The README's first line,
# word for word, is also the package's description on PyPI.
OWNER_LINE = ("**Know where your research stands.** Your AI agent records what was tried, what the results show, and "
              "what's still open, with the code and settings.")
SECTIONS = ["Quick start: a sample study", "Use it on your own project", "What rb shows you", "How it works",
            "The record doesn't take the agent's word", "What it doesn't do", "Install by hand", "More"]
# The owner's quick start (Nov 12 2026), word for word on every public surface: the README, the landing, the docs home
# and the sample study's README (/srv/rb/notes/QUICKSTART.md). Its words change on all of them together, never here alone.
QUICKSTART_HEADING = 'Quick start: a sample study'
QUICKSTART_LEAD = ("Try Rabbit Brain on a sample study, so you don't need to set up any experiments or use your own research. "
                   'Just open Claude Code or Codex in an empty folder and paste the prompt below.')
QUICKSTART_PROMPT = "\n".join([
    'Clone https://github.com/rabbit-brain/think-step-by-step and work inside it.',
    'Install rabbit-brain from PyPI into a virtual environment in that folder.',
    "It's open source: https://github.com/rabbit-brain/rb",
    "Start rb login in the background and show me the sign-in link it prints. I'll sign in.",
    "When I'm signed in, run rb sync and give me the link it prints.",
    "Run rb context and tell me in three lines what the study found and what's still open.",
    "Don't run any experiments.",
])
QUICKSTART_AFTER = ("Your agent gives you a sign-in link (an email code, no password). Then the study opens in your private "
                    "account on rabbitbrain.ai. No account? Ask your agent to run rb page instead.")
RAW = "https://raw.githubusercontent.com/rabbit-brain/rb/main/"
BLOB = "https://github.com/rabbit-brain/rb/blob/main/"


def readme_section(heading: str) -> str:
    """The text of the README's `## <heading>` section, up to the next one."""
    return README.split(f"\n## {heading}\n\n", 1)[1].split("\n## ", 1)[0]


def readme_prose(text: str) -> list[str]:
    """The README's words a visitor reads as prose: not its commands and prompts (fenced blocks), its picture or its
    headings."""
    text = re.sub(r"^ *```.*?^ *```[^\n]*$", "", text, flags=re.S | re.M)
    text = re.sub(r"<picture>.*?</picture>", "", text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.findall(r"[^\s#*>`|-]\S*", text)


def test_the_readme_opens_with_the_owner_s_line_and_keeps_its_structure():
    """Nov 12, the owner: the README is plain, specific sentences with its structure kept. It opens with the owner's
    line, word for word, which is the package's description too; its sections are these, in this order; its prose stays
    at 550 words or fewer (457 by this count when it was written; prompts, commands and headings not counted), so it does not grow back; and
    More links the docs and the repository's own notes."""
    first = README.split("\n\n")[1]
    assert README.startswith("# Rabbit Brain\n\n") and first == OWNER_LINE, first
    description = re.search(r'^description = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), flags=re.M).group(1)
    assert description == OWNER_LINE.replace("**", ""), description
    assert re.findall(r"^## (.+)$", README, flags=re.M) == SECTIONS
    words = readme_prose(README)
    assert len(words) <= 550, f"the README's prose is {len(words)} words; keep it at 550 or fewer and move the rest to the docs"
    more = readme_section("More")
    assert "[Docs](https://rabbitbrain.ai/docs/)" in more
    for name in ("PRIVACY.md", "CHANGELOG.md", "CONTRIBUTING.md"):
        assert f"[{name}]({BLOB}{name})" in more, name
    assert README.rstrip("\n").endswith(f"License: [Apache-2.0]({BLOB}LICENSE)")


def readme_commands() -> list[str]:
    """Every command the README shows in its ```sh blocks, an alternative named in a comment (`# or: ...`) included."""
    found = []
    for block in re.findall(r"```sh\n(.*?)```", README, flags=re.S):
        for line in block.splitlines():
            line, _, comment = line.strip().partition("#")
            comment = comment.strip()
            found += [part.strip() for part in (line, comment.removeprefix("or:") if comment.startswith("or:") else "") if part.strip()]
    return found


def test_every_command_in_the_readme_is_real(capsys):
    """Each command the README shows runs as written: an rb line is one rb accepts, uvx runs rb from the package this
    repository builds, and an install names that package. The work typed by hand starts with rb init, and Install by
    hand gives the tour. Every `rb ...` code span the README gives in a sentence or a prompt names a command rb has, or one it plans and answers E_NOT_AVAILABLE for (`rb paper`)."""
    package = re.search(r'^name = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), flags=re.M).group(1)
    commands = readme_commands()
    parser = cli_parser.build_parser()
    rb_lines = []
    for command in commands:
        argv = shlex.split(re.sub(r"<[^>]*>", "X", command))
        if argv[:3] == ["uvx", "--from", package] and argv[3] == "rb":
            rb_lines.append(argv[4:])
        elif argv[0] == "rb":
            rb_lines.append(argv[1:])
        else:
            assert argv in (["uv", "tool", "install", package], ["pip", "install", package]), f"the README shows {command}"
    for argv in rb_lines:
        try:
            parser.parse_args(argv)
        except cli_parser.UsageError as exc:
            raise AssertionError(f"the README shows a command rb does not accept: rb {' '.join(argv)}\n  {exc.message}") from None
    assert rb_lines[0][0] == "init" and rb_lines[-1] == ["tour"], rb_lines
    assert {"uv tool install " + package, "pip install " + package} <= set(commands)
    real = registered_commands()
    named = re.findall(r"`rb ([a-z]+(?: [a-z]+)?)", README)
    assert named
    for words in named:
        base = words.split()[0]
        assert words in real or base in real or answers_not_available(base, capsys), f"the README names `rb {words}`, which rb does not have"


def png_text(path: Path) -> dict:
    """The tEXt chunks of a PNG file."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    text, at = {}, 8
    while at < len(data):
        size, kind = int.from_bytes(data[at:at + 4], "big"), data[at + 4:at + 8]
        if kind == b"tEXt":
            key, _, value = data[at + 8:at + 8 + size].partition(b"\0")
            text[key.decode("latin-1")] = value.decode("latin-1")
        at += 12 + size
    return text


def test_the_readme_s_picture_is_rb_open_in_light_and_dark():
    """The picture is what a visitor sees on first open: rb open's first screen, made by tools-readme/picture.sh from a
    build (its tEXt chunk says which), a light and a dark image chosen by the reader's colour scheme, and an <img>
    fallback, which is what PyPI shows. Each under 300 KB, at an absolute address PyPI can load, with alt text that says
    what it shows."""
    [picture] = re.findall(r"<picture>.*?</picture>", README, flags=re.S)
    sources = dict(re.findall(r'<source media="\(prefers-color-scheme: (dark|light)\)" srcset="([^"]+)">', picture))
    [(alt, src)] = re.findall(r'<img alt="([^"]+)" src="([^"]+)"', picture)
    assert set(sources) == {"dark", "light"} and src == sources["light"] and sources["dark"] != src
    assert len(alt.split()) >= 10 and "rb open" in alt, alt
    for scheme, url in sources.items():
        assert url.startswith(RAW), url
        image = ROOT / url[len(RAW):]
        assert image.is_file() and image.stat().st_size < 300_000, (image, image.stat().st_size if image.exists() else None)
        made = png_text(image).get("Source", "")
        assert made.startswith("rb open, first screen, ") and f"{scheme} scheme" in made and "rabbit-brain " in made, made


def test_the_readme_s_links_to_repository_files_resolve():
    """Every address the README gives into this repository (a file on main, or its raw bytes) names a file that is here."""
    urls = re.findall(r"https://(?:raw\.githubusercontent\.com|github\.com)/rabbit-brain/rb/\S+?(?=[\s\"<>)]|$)", README)
    assert urls
    for url in urls:
        path = url[len(RAW):] if url.startswith(RAW) else url[len(BLOB):] if url.startswith(BLOB) else None
        assert path and (ROOT / path).exists(), url


def test_the_readme_holds_the_owner_s_quick_start():
    """Nov 12, the owner: "somewhere in our landing or doc or github or all we need a quick start as started .... a
    sample study, so you don't need to set up any experiments or use your own research". The README's first section
    after its picture is the owner's quick start, word for word: its heading, its lead, the prompt byte for byte in a
    fenced block of its own (GitHub gives it a copy button; its line 2 is two lines since Nov 12, so no line runs far
    off the page), and the line after it, and nothing more."""
    assert re.findall(r"^## (.+)$", README, flags=re.M)[0] == QUICKSTART_HEADING
    body = readme_section(QUICKSTART_HEADING)
    assert body == f"{QUICKSTART_LEAD}\n\n```\n{QUICKSTART_PROMPT}\n```\n\n{QUICKSTART_AFTER}\n", body
    assert README.index("</picture>") < README.index(f"## {QUICKSTART_HEADING}") < README.index("## Use it on your own project")


def test_the_readme_s_own_project_prompt_names_what_an_agent_runs_first():
    """Use it on your own project: one fenced prompt for the agent, and `rb open` for the user, in their own terminal
    (rb open refuses an agent session). The prompt starts the record from the start protocol and rb context, leaves the
    calls on results to the user with the `rb confirm` command, and names the page and the sync it can write."""
    body = readme_section("Use it on your own project")
    assert body.startswith("Open Claude Code or Codex in your project and paste this prompt. Then run `rb open` in your own terminal to see the record.\n\n```\n")
    [prompt] = re.findall(r"^```\n(.*?)^```$", body, flags=re.S | re.M)
    for said in ("`rb docs --start`", "`rb context --model <your model id>`", "`rb journal run`", "Calls on results are mine.",
                 "`rb confirm`", "`rb page`", "`rb login`", "`rb sync`", "`rb status` exactly as printed"):
        assert said in prompt, said
    assert "rb open" not in prompt and "rb ui" not in prompt


def test_the_readme_says_what_the_record_does_and_does_not_do():
    """The README's two lists, as the manual's Boundaries says them: what the record does not take on the agent's word,
    and what rb does not do. The manual keeps the long form (test_the_manual_states_rb_s_limits)."""
    record = readme_section("The record doesn't take the agent's word")
    for said in ("- Settings are checked against your files. A value rb can't find where it is cited shows as provisional.",
                 "- Results are worked out from the runs each time you read them. None is stored.",
                 "- A pass mark written after its results says so on every reading.",
                 "- Every decision keeps who made it and why."):
        assert said in record.splitlines(), said
    limits = readme_section("What it doesn't do")
    for said in ("- It doesn't choose or launch your experiments. `rb journal run` only wraps a command you give it.",
                 "- It doesn't train or certify models.",
                 "- It can't record a choice that only lived in the chat.",
                 "- It sees only your repository and what runs through `rb journal run`."):
        assert said in limits.splitlines(), said
    how = readme_section("How it works")
    assert "4. You make the final decisions. If your agent accepts or rejects a result, it waits until you run `rb confirm`." in how


TRAIN = "optim:\n  name: sgd\n  lr: 0.1\n  momentum: 0.9\ndata:\n  dataset: imagenet\n  batch_size: 256\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="the work typed by hand runs in a git repository")
def test_the_readme_s_finding_is_what_rb_show_prints_after_its_work_typed_by_hand(workdir, monkeypatch, tmp_path_factory, capsys):
    """The README's `rb show` block (What rb shows you) is real output of this build: the work typed by hand (How it
    works), run as written in the layout it describes (its configs in configs/, each seed's results in out/seed<N>/),
    seeds 2 and 3 recorded as its seed 1 is, then rb show of the criterion, line for line once the ids are named in
    order. The sentence under the block says the numbers the block shows. Since the card (int, Nov 12), `rb show
    <criterion>` prints the card and `rb show <criterion> --long` the axes the README shows, so the block is held to
    the long form, and the sentence above it names `rb show <id> --long` (Nov 13)."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True)  # noqa: E731
    how = readme_section("How it works")
    assert "The same work typed by hand, in a repository with its configs in `configs/` and each seed's results in `out/seed<N>/`:" in how
    by_hand = shell_command_lines(how)
    shows = readme_section("What rb shows you")
    assert "Here is `rb show <id> --long` on the INT8 example further down:" in shows
    shown = shows.split("```\n", 1)[1].split("```", 1)[0]

    def seed(n, fp32, int8):
        Path(f"out/seed{n}").mkdir(parents=True)
        Path(f"out/seed{n}/config.yaml").write_text(TRAIN + f"seed: {n}\n", encoding="utf-8")
        Path(f"out/seed{n}/metrics.json").write_text(json.dumps({"fp32": {"val_loss": fp32}, "int8": {"val_loss": int8}}), encoding="utf-8")

    git("init", "-q")
    Path("configs").mkdir()
    for name, text in (("train", TRAIN), ("fp32", "precision: fp32\n"), ("int8", "precision: int8\n")):
        Path(f"configs/{name}.yaml").write_text(text, encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "configs")
    time.sleep(1.1)                                     # the first seed's output comes after the configs' commit
    seed(1, 1.2, 1.23)
    git("add", "-A")
    git("commit", "-qm", "the first seed")
    assert by_hand[0].startswith("rb init ") and any(line.startswith("rb run add ") for line in by_hand)
    for line in by_hand:
        assert main(shlex.split(line)[1:]) == 0, line
    seed(2, 1.21, 1.23)
    seed(3, 1.19, 1.22)
    for n in (2, 3):
        assert main(["run", "add", "int8", "--from", f"out/seed{n}/metrics.json", "--set", f"seed={n}", "--config", f"out/seed{n}/config.yaml"]) == 0
    capsys.readouterr()
    assert main(["status", "--json"]) == 0
    criterion = Envelope.model_validate_json(capsys.readouterr().out).data["findings"][0]["criterion"]
    assert main(["show", criterion, "--long"]) == 0
    printed = capsys.readouterr().out

    def named(text):
        ids = [re.match(r"Criterion (\S+) on", text).group(1)] + re.findall(r"^  (run\S+):", text, flags=re.M)
        for n, i in enumerate(ids):
            text = re.sub(rf"\b{i}\b", f"<id{n}>", text)
        return text
    assert named(shown) == named(printed)
    estimate = float(re.search(r"estimate \+([0-9.]+) ≤ 0\.05", shown).group(1))
    assert (f"The rule allows 0.05 more validation loss. Over 3 seeds INT8 adds {estimate:.3f}, so the rule is met. Seed 1 "
            "existed before the rule was written, and rb says so on every reading.") in " ".join(shows.split())
