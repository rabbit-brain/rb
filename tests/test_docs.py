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
    be a command line rb accepts, with <placeholders> standing for a value and [--optional parts] left out."""
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
    read as well as on a finding's problem: the gate's line and every line of the manual and the README that says what
    the CI gate fails on say so."""
    [gate] = [line for line in DOC.splitlines() if line.startswith("- `integrity`: ")]
    assert "question link" in gate and "relation" in gate
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ci = [line for text in (DOC, readme) for line in text.splitlines() if "the CI gate" in line]
    assert len(ci) >= 2 and all("question link" in line and "relation" in line for line in ci), ci


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
    main(["status"])
    lines = capsys.readouterr().out.splitlines()
    parts = [("the project, and the last", lambda line: line.startswith("Ordering · ")),
             ("where each question stands", lambda line: line.startswith("Does the order hold? (")),
             ("the tally of the criteria", lambda line: line.startswith("Criteria: ")),
             ("**Needs a human**", lambda line: line.startswith("Needs a human (")),
             ("**Can be done now**", lambda line: line.startswith("Can be done now (")),
             ("each criterion on its axes, with its integrity problems", lambda line: line == "Criteria"),
             ("each experiment with its state", lambda line: line == "Experiments"),
             ("the runs, and whether", lambda line: line.startswith("Runs: ")),
             ("the analyses, and which", lambda line: line == "Analyses"),
             ("the notes on the project", lambda line: line == "Notes on the project"),
             ("each resource with its availability", lambda line: line == "Resources")]
    listed = DOC[DOC.index("`rb status [<exp>]` prints, in order:"):DOC.index("**What changed since you last looked.**")]
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


def test_the_readme_says_what_rb_cannot_promise_of_a_design_and_what_it_does():
    """Oct 13: where the README says what rb keeps honest, it says in plain sentences what rb cannot promise of a
    method (a choice that only lived in the chat, whether a method fits the question) and what it does, and the manual
    says the same limit beside the design's standing."""
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    for said in ("It cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question.",
                 "nothing a session wrote reads as yours until you confirm it, every setting says where its value came from, and every finding is "
                 "computed from the files, not from the agent's account.",
                 "An experiment's design an agent wrote any part of (its hypothesis, settings, criteria and gate) reads `design not confirmed` until you confirm it"):
        assert said in readme, said
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


def test_the_manual_and_the_readme_state_rb_s_limits_and_where_it_looks():
    """Oct 14, decision 6: the manual's boundaries carry the Oct 13 line (what rb cannot promise of a method, and what it
    does), and the README says where rb looks on its own and what it reads only where the record points to it."""
    bounds = " ".join(DOC.split("## Boundaries", 1)[1].split("## ", 1)[0].split())
    for said in ("It cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question.",
                 "What it does: nothing a session wrote reads as the user's until they confirm it, every setting says where its value came from, "
                 "and every finding is computed from the files, not from the agent's account."):
        assert said in bounds, said
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert ("on its own it looks in `out/`, `outputs/`, `results/`, `eval-out/`, `metrics/` and any folder a recorded run named, at the files in "
            "the repository's top folder, and at .json, .yaml and .toml files git shows as new or changed, and it reads what code, configs, docs "
            "and database rows say only where the record points to them.") in readme


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


def test_the_readme_says_how_to_keep_going_after_uvx():
    """After uvx, rb is not on the path: the README says how to run what the tour suggests next."""
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert ("After `uvx`, `rb` is not on your path: run the tour's next commands as `uvx --from rabbit-brain rb ui`, "
            "or keep `rb` with `uv tool install rabbit-brain`.") in readme


TRAIN = "optim:\n  name: sgd\n  lr: 0.1\n  momentum: 0.9\ndata:\n  dataset: imagenet\n  batch_size: 256\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="the Quick start runs in a git repository")
def test_the_readme_s_finding_is_what_rb_show_prints_after_its_quick_start(workdir, monkeypatch, tmp_path_factory, capsys):
    """The README's `rb show` block is real output of this build: its Quick start, run as written in the layout it
    describes (each out/seed<N>/metrics.json in the shape it shows), seeds 2 and 3 recorded the same way, then rb show of
    the criterion, line for line once the ids are named in order."""
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.com\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = lambda *a: subprocess.run(["git", *a], check=True, capture_output=True)  # noqa: E731
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quick = shell_command_lines(readme.split("## Quick start", 1)[1].split("## Reading a finding", 1)[0])
    shown = readme.split("## Reading a finding", 1)[1].split("```\n", 1)[1].split("```", 1)[0]
    shape = re.search(r"`(\{\"fp32\": \{\"val_loss\": [0-9.]+\}, \"int8\": \{\"val_loss\": [0-9.]+\}\})` gives `fp32.val_loss` and `int8.val_loss`", readme)
    assert shape and json.loads(shape.group(1)) == {"fp32": {"val_loss": 1.2}, "int8": {"val_loss": 1.23}}

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
    for line in quick:
        assert main(shlex.split(line)[1:]) == 0, line
    seed(2, 1.21, 1.23)
    seed(3, 1.19, 1.22)
    for n in (2, 3):
        assert main(["run", "add", "int8", "--from", f"out/seed{n}/metrics.json", "--set", f"seed={n}", "--config", f"out/seed{n}/config.yaml"]) == 0
    capsys.readouterr()
    assert main(["status", "--json"]) == 0
    criterion = Envelope.model_validate_json(capsys.readouterr().out).data["findings"][0]["criterion"]
    assert main(["show", criterion]) == 0
    printed = capsys.readouterr().out

    def named(text):
        ids = [re.match(r"Criterion (\S+) on", text).group(1)] + re.findall(r"^  (run\S+):", text, flags=re.M)
        for n, i in enumerate(ids):
            text = re.sub(rf"\b{i}\b", f"<id{n}>", text)
        return text
    assert named(shown) == named(printed)


def test_the_readme_says_where_a_waiting_call_is_listed():
    """rb status lists a call waiting on the user under Proposed, not made, and names rb confirm first."""
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "`rb status` lists each one waiting under **Proposed, not made**, with that command, and its Next line names `rb confirm` first." in readme
    assert "lists the ones waiting under **Needs a human**" not in readme


def test_llms_txt_states_the_limits_the_readme_states():
    """llms.txt, which agents and crawlers read, says what rb cannot do, where it looks and what a receipt is, as the
    README does."""
    llms = " ".join((ROOT / "llms.txt").read_text(encoding="utf-8").split())
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    for said in ("It cannot make an agent record a choice that only lived in the chat, and it cannot judge whether a method fits the question.",
                 "It sees only what is in the repository and what runs through `rb journal run`: on its own it looks in `out/`, `outputs/`, "
                 "`results/`, `eval-out/`, `metrics/` and any folder a recorded run named,",
                 "not proof of what produced it."):
        assert said in llms and said in readme, said


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
