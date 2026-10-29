"""The way into a project already under way: `rb docs --start` prints the start protocol (the MCP resource rb://start
too); `rb status` and `rb context` point at it while the record holds no question in a repository with history, and
E_NO_PROJECT, `rb compile` and `rb reconcile` with no record point at it; one paste line starts the work everywhere it
is given; and `rb init` run inside an agent session installs nothing that session would not load."""
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import Mcp, as_the_user, at_home, environment, home, main
from test_export import SERVED, served_at  # noqa: F401 (origin at an https address, served from a bare repository)

ROOT = Path(__file__).resolve().parents[1]
PASTE = "Use Rabbit Brain to track this: read rb context, then record where the work stands."
START = (ROOT / "rust" / "src" / "assets" / "start.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture
def rb(workdir, monkeypatch, capsys, tmp_path_factory):
    """ana at her own terminal, in a folder git does not know yet. rb(...) is rb ... --json: (exit code, envelope);
    rb.text(...) is what rb ... prints."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    home_dir = tmp_path_factory.mktemp("home")
    at_home(monkeypatch, home_dir)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("[user]\n\tname = ana\n\temail = ana@example.com\n[commit]\n\tgpgsign = false\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def call(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    def text(*argv):
        main(list(argv))
        out = capsys.readouterr()
        return out.out + out.err

    call.text = text
    call.cwd = workdir
    return call


def git(*argv: str) -> None:
    subprocess.run(["git", *argv], check=True, capture_output=True)


def committed(rel: str, body: str) -> None:
    p = Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    git("add", rel)
    git("commit", "-q", "-m", f"add {rel}")


def pointers(env) -> list:
    return [i for i in env.data["open"] if i["code"] == "no_question"]


# ---------------------------------------------------------------- the protocol


def test_the_protocol_is_short_asks_nothing_and_says_what_the_owner_decided(rb):
    code, env = rb("docs", "--start")
    assert code == 0 and env.data["markdown"] == START
    assert rb.text("docs", "--start").rstrip("\n") == START.rstrip("\n")
    lines = START.rstrip("\n").splitlines()
    assert len(lines) < 150 and max(len(x) for x in lines) <= 80
    said = flat(START)
    for rule in ("Ask the researcher nothing.", "Your memory only says where to look: the files decide",
                 "rb context --model <your model id>", "rb reconcile", "rb compile",
                 "Record the frontier first", "each linked to the question its comparison answers (rb criterion add ... -q <question>)",
                 "Older work waits and can be backfilled later", "--from <db> --table <table> --where <column>=<value>", "--produced-at",
                 "Never write an export to stand in for a store", "--dry-run", "Take criteria from what is written, a document or a plan the frontier rests on",
                 "A threshold no document states stays unknown for the runs already made", "--better",
                 "--command-source <file>:<line>", "is a recipe", "Record no call from memory", "--words \"<their words>\"",
                 "waits for their one confirmation",
                 "It waits for the same one confirmation, whoever ran rb init: rb says what git shows of the file",
                 "In a running session it installs nothing; keep the commands it gives for later sessions",
                 "when rb init installed nothing, the commands it gave for later sessions",
                 "A note names no one and says no memory",
                 "Never name yourself, your model or your session", "rb show <the question's id>",
                 "the calls and experiment designs (a gate waits with its design) that wait for their authority: rb confirm lists each, in their own terminal, with the command that confirms it", "what you left out"):
        assert rule in said, rule
    # the order rb can write them in: a rule needs its experiment (-e), a run its rule
    order = [said.index(x) for x in ("its question (rb question add)", "its experiments", "the rules it turns on", "then only the runs")]
    assert order == sorted(order)
    # nothing is asked of the researcher up front, and rb's words stay its own
    assert "?" not in START and "in one message" not in said
    for word in ("\u2014", "verified", "supported", "established", "contradicted", "the person", "a person"):
        assert word not in START, word


def test_the_protocol_checks_memory_first_sets_a_stated_gate_rounds_a_cited_figure_and_asks_nothing():
    """The Oct 11 acceptance round: no trial checked the earlier stage a memory claim rested on, none set the gate the
    researcher stated, each gave a rounded paper figure half its last digit, and each report asked 3 or 4 questions."""
    said = flat(START)
    check = said.index("Check each claim from memory the frontier rests on now, an earlier stage's included, before the first rb status (step 6): "
                        "record the rule rb reads over only the runs the claim names")
    assert check < said.index("Record the frontier first")
    assert ("A next step the researcher stated for when the comparison holds (\"if it holds, we ...\"), earlier in this chat or in a file, is "
            "the gate of the experiment that holds the comparison: rb experiment gate <exp> --if-holds \"<the action>\" --words \"<their words>\": "
            "--if-holds is what they would do (\"we ship INT8\"), and the whole sentence goes in --words (a file's: --why \"<file>:<line>\"). "
            "No threshold, no unsaid --if-not, no next step no one stated") in said
    assert ("Its tolerance is the rounding it carries: half a unit of the last digit for each rounded figure it comes from "
            "(-0.006 as 0.755 - 0.761 takes --tolerance 0.001)") in said
    assert ("--set <name>=@<column> (@<table>.<column> from the row another table links). A row's setting: "
            "--source <db>#<table>[<column>=<value>].<column>.") in said
    assert "read runs from their stores, with per-run values and settings from the row" in flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("check each claim from memory the frontier rests on, an earlier stage's included, by recording the rule rb reads over the runs it names",
                 "a next step the researcher stated for when the comparison holds, earlier in the chat or in a file, is the gate of the experiment "
                 "that holds the comparison",
                 "give `--tolerance` half a unit of its last digit for each rounded figure it comes from, so a match within rounding reads `reproduced`"):
        assert rule in agents, rule


def test_the_protocol_takes_the_current_frontier_and_the_report_ends_with_no_question():
    """Oct 11 evening and round 5: the scope is named the current frontier and inferred from the session's context and
    the files (a cold session took the paper's headline question); the report says what it took and why, and how to
    change it; a dated decision a committed file states is recorded as a past call, which the report points at as the
    one thing that needs the researcher's authority; each rule is linked to the question on the frontier, with no
    narrower question made to hold it; a memory claim is checked by a rule rb reads; a note that no file records
    something names the files read; and the report ends with no question (round 5 asked 8)."""
    said = flat(START)
    for rule in ("The scope is the current frontier: the question actively worked on, with the experiments, runs, findings and open calls around it",
                 "Infer the current frontier, never assert it: from the session's context and the files, or the files alone when you have no context",
                 "Uncommitted plans and scripts say what is on now first, then the latest activity in the store, then recent commits",
                 "record the rule rb reads over only the runs the claim names, so rb computes the reading",
                 "Your own query alone checks nothing", "Add no narrower question to hold a comparison",
                 "A dated decision a committed file states is a past call on the experiment it concerns: --past --when <date> --source <file>:<line> --quote \"<its words>\"",
                 "\"no file I read records this: <files>\"",
                 "Your interpretation is a note, or --when-met, with what it rests on; rb shows it as not confirmed",
                 "It ends with no question at all: what you would ask stays open in the record",
                 "Never ask for a threshold, a margin, housekeeping or a past call from memory",
                 "what you took as the current frontier, by its question id, and why (the commits, files and runs it rests on), and the scope used; to change either, they tell the session, and older work can be backfilled later",
                 "the calls and experiment designs (a gate waits with its design) that wait for their authority"):
        assert rule in said, rule
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("the scope is the current frontier", "infer it from the session's context and the files, or the files alone, never assert it",
                 "The report ends with no question at all", "no narrower question is made to hold a comparison",
                 "a dated decision a committed file states is recorded as a past call from that file"):
        assert rule in agents, rule


def test_the_protocol_holds_a_shared_arm_once_scopes_a_waiting_stage_and_declares_no_rule_of_its_own():
    """Review of round 6, two runs of the protocol on a fresh copy: a shared arm went into two experiments (three
    rows read twice); step 4 asked for every Stage 1 cell where the scope says older work waits; a direction rule the
    session declared "for the next runs" read the May runs and put a call in front of the researcher; the frontier's
    signals had no order; a gate's words came only from memory; and the past call's command left out --why."""
    said = flat(START)
    for rule in ("Comparisons that share an arm share one experiment, a variant per arm, each rule with its own --subject and --reference: read a row once",
                 "A direction rule (--better) reads them too: declare one only where a document, or a plan the frontier rests on",
                 "Leave the rest to the researcher",
                 "Uncommitted plans and scripts say what is on now first, then the latest activity in the store, then recent commits",
                 "--past --when <date> --source <file>:<line> --quote \"<its words>\" --why \"<file>:<line>\"",
                 "point them at rb show <the question's id>"):
        assert rule in said, rule
    assert "For the next runs, declare a direction rule" not in said and "even where that stage waits" not in said


def test_the_protocol_checks_a_claim_the_frontier_rests_on_now_and_records_a_narrowing_as_continue():
    """Round 6: both memory trials left a claim the frontier rested on for backfill, as step 4's last sentence let
    them; all three parked an experiment whose other arms kept running; all three took a sentence about another stage
    as a rule's statement; one verified the settings of past runs from a plan for the next ones; the live trials read
    no standing for 7 to 11 minutes; the reports said memory's words as "your words" and opened on a finding rb did not
    compute; and step 8 left open whether a next step stated earlier in the chat counts."""
    said = flat(START)
    for rule in ("Backfill is for older work the frontier does not rest on, never for a claim it rests on",
                 "Where no rule reads it, a note names the file and rows (step 11)",
                 "A dated narrowing (an arm or a range dropped while the rest went on) is no park: record continue on the experiment that holds "
                 "the dropped arms, its quote saying what was dropped, so one confirm parks nothing that kept running",
                 "declare one only where a document, or a plan the frontier rests on (an uncommitted script, a next step stated in this chat), "
                 "states the direction for the conditions the rule reads, in its words, with its file and line or --words; never with a sentence about "
                 "other conditions or another stage",
                 "each from a row, a config or a file at a commit before the run: a plan's lines set only the runs it will launch",
                 "Then run rb status, before any note: the standing comes first",
                 "as findings, only what rb status computes, and plainly what it does not compute yet",
                 "Say the researcher's words memory quotes \"as you said earlier\"",
                 "A next step the researcher stated for when the comparison holds (\"if it holds, we ...\"), earlier in this chat or in a file"):
        assert rule in said, rule
    assert "waits for backfill" not in said and "none only memory recalls" not in said
    assert said.index("Then run rb status, before any note") < said.index("11. A claim no file states goes in a note")
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("backfill is for older work the frontier does not rest on, never for a claim it rests on",
                 "a dated narrowing (an arm or a range dropped while the rest went on) is `continue` on the experiment that holds the dropped arms",
                 "never with a sentence about other conditions or another stage as its statement", "since a plan's lines set only the runs it will launch",
                 "run `rb status` once the frontier's runs and rules are in, before the notes",
                 "as findings, only what `rb status` computes", "never \"your words\"", "no next step no one stated",
                 "under 150 lines"):
        assert rule in agents, rule


def test_the_protocol_reads_an_ordering_sets_settings_first_and_states_rules_plainly():
    """Review of round 6's build: the rule route step 4 named could not compute an ordering over fixed design cells
    (--better reads an interval those cells never give); a setting added after the runs voided every reading; step 7's
    "from documents only" stood against a next step stated in the chat; a criterion took the chat's first-person
    sentence as its statement; a note route had the session write its own comparison; and the report called the
    confirm the one thing that needs the researcher while rb status listed more under Needs judgment."""
    said = flat(START)
    for rule in ("An ordering in every cell is one rule per pair and metric, --at-most 0 --each (--at-least 0 where higher is better): "
                 "the researcher's words memory quotes in --words",
                 "before the first rb status (step 6)",
                 "Set the shared settings before the runs (one set after reads them as under an earlier spec)",
                 "a statement says in plain words what the rule checks, the words it rests on in its quote or --words",
                 "the calls and experiment designs (a gate waits with its design) that wait for their authority: rb confirm lists each, in their own terminal, with the command that confirms it; and what rb status lists under Needs judgment"):
        assert rule in said, rule
    for gone in ("Take criteria from documents only", "records what they say", "the one thing that needs their authority",
                 # recheck of group E: a design is read first, as rb confirm lists it, never confirmed unread with --all
                 "rb confirm --all"):
        assert gone not in said, gone
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("an ordering in every cell is one `--at-most 0 --each` rule per pair and metric", "set the shared settings before the runs",
                 "each statement saying in plain words what its rule checks", "and what `rb status` lists under Needs judgment"):
        assert rule in agents, rule


def test_the_protocol_keeps_session_text_off_the_researcher_and_the_cold_question_on_the_files():
    """Round 7: a session wrote "The researcher's rule is ...", "left to the researcher", "stated in this chat" and "the
    account given in the session" in notes and reasons; a cold session narrowed the question with a premise no file
    states and declared direction rules from a sentence about conditions the rules never read; and a report asked the
    researcher to confirm a gate no command confirmed."""
    said = flat(START)
    for rule in ("Your text (a note, a --why, a statement, a gate's reason) calls nothing the researcher's and names no chat or session as "
                 "its source; --words is only the researcher's words memory quotes; memory's own claims stay in the statement",
                 "With no context, the question is the files' own claim turned into a question, with every factor it names and no premise no file states",
                 "states the direction for the conditions the rule reads",
                 "never with a sentence about other conditions or another stage",
                 "the calls and experiment designs (a gate waits with its design) that wait for their authority: rb confirm lists each, in their own terminal, with the command that confirms it; and what rb status lists under "
                 "Needs judgment; nothing no command confirms"):
        assert rule in said, rule
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("a session's text calls nothing the researcher's and names no chat or session as its source; `--words` is only the researcher's words memory quotes, and memory's own claims stay in the statement",
                 "with no context, the question is the files' own claim turned into a question, with every factor it names and no premise no file states",
                 "states the direction for the conditions the rule reads",
                 "the calls and experiment designs (a gate waits with its design) that wait for the researcher's authority (`rb confirm` lists each with the command that confirms it",
                 "and nothing no command confirms"):
        assert rule in agents, rule


def test_a_dated_narrowing_recorded_as_continue_leaves_the_arms_that_kept_running_on(rb, monkeypatch):
    """Step 10: rb decide takes continue as a past call from the file's line, on the experiment that holds the dropped
    arm. It waits for one confirmation, and once confirmed the experiment reads continuing, with no runs listed as made
    after a park, since nothing was parked."""
    git("init", "-q")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-08-20T12:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-08-20T12:00:00+00:00")
    committed("docs/decisions.md", "# Decisions\n\n2026-08-14: we drop the fp16 arm; int8 goes on against fp32.\n")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    assert rb("experiment", "add", "INT8 and FP16 against FP32", "--id", "prec", "--baseline", "fp32", "--candidate", "int8", "--candidate", "fp16")[0] == 0
    assert rb("run", "add", "prec", "fp32.top1=0.761", "int8.top1=0.755", "--produced-at", "2026-09-01T10:00:00Z")[0] == 0
    monkeypatch.delenv("RB_ACTOR")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-start-narrow")
    code, env = rb("decide", "prec", "continue", "--why", "docs/decisions.md:3", "--past", "--when", "2026-08-14",
                   "--source", "docs/decisions.md:3", "--quote", "we drop the fp16 arm; int8 goes on against fp32")
    assert code == 0 and env.data["authority"]["state"] == "past_unconfirmed"
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    assert rb("confirm", env.data["object"]["id"])[0] == 0
    code, st = rb("status")
    assert {e["id"]: e["state"] for e in st.data["experiments"]}["prec"] == "open"
    assert [d["action"] for d in st.data["decisions"]] == ["continue"] and all("runs_after" not in d for d in st.data["decisions"])
    assert 'call: continued 2026-08-14 (a past call from docs/decisions.md:3, "we drop the fp16 arm; int8 goes on against fp32"' in rb.text("status")


def test_the_manual_skill_and_llms_txt_point_at_the_protocol():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    nothing = agents.index("What no file says is the agent's to record")
    section = agents.index("### Starting the record in a project already under way: `rb docs --start`")
    assert nothing < section and agents.index("### ", nothing) == section       # beside "What no file says"
    for name in ("SKILL.md", "llms.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert sum("rb docs --start" in line for line in text.splitlines()) == 1, name


def test_mcp_serves_the_protocol_as_rb_start(rb, workdir):
    server = Mcp(root=workdir, env={**environment(), "RB_SESSION": "sess-start-1"})
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "start-client"}}})
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})["result"]["resources"]
        assert {"uri": "rb://start", "name": "start", "mimeType": "text/markdown"}.items() <= next(r for r in listed if r["uri"] == "rb://start").items()
        read = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "rb://start"}})["result"]["contents"][0]
        assert read["text"] == START and read["mimeType"] == "text/markdown"
        instructions = server.handle({"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert "rb://start" in instructions["result"]["instructions"]
    finally:
        server.close()



def test_a_documented_past_call_waits_whoever_began_the_record_as_the_protocol_says(rb, monkeypatch):
    """Step 1 has a session with no record run rb init. A past call a document committed long before states waits for
    the one confirmation, as it does when the researcher began the record, and step 9 says so."""
    git("init", "-q")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-08-20T12:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-08-20T12:00:00+00:00")
    committed("docs/decisions.md", "# Decisions\n\n2026-08-14: we stop the fp16 line; fp16 adds nothing over int8.\n")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("RB_ACTOR")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-start-7c1d")
    assert rb("init", "INT8 ResNet-50 on ImageNet")[0] == 0
    assert rb("experiment", "add", "FP16 against FP32", "--id", "fp16", "--baseline", "fp32", "--candidate", "fp16")[0] == 0
    code, env = rb("decide", "fp16", "stop", "--why", "fp16 adds nothing over int8", "--past", "--when", "2026-08-14",
                   "--source", "docs/decisions.md:3", "--quote", "we stop the fp16 line; fp16 adds nothing over int8")
    assert code == 0 and env.data["authority"]["state"] == "past_unconfirmed"
    assert env.data["authority"]["basis"].startswith("the quote is in docs/decisions.md:3, committed 2026-08-20 (")
    assert "It waits for the same one confirmation, whoever ran rb init" in flat(START)



PLAN = "# Plan\n\nINT8 ships if it loses at most 0.01 top-1 against FP32 on the validation set.\n<!-- at most 0.002 -->\nOr 1% at worst.\n"
RULE = "INT8 ships if it loses at most 0.01 top-1 against FP32 on the validation set."


@pytest.mark.parametrize("threshold, why, refused", [
    ("-0.01", "docs/plan.md:3", ""),
    ("-0.01", "docs/plan.md:5", ""),                         # 1% states 0.01
    ("-0.002", "the plan allows more, but we hold it to 0.002", ""),     # a reason in words is not checked
    ("-0.002", "docs/plan.md:3", "docs/plan.md:3, and that line does not state its threshold 0.002."),
    ("-0.002", "docs/plan.md:4", "docs/plan.md:4, and that line is a comment, which states nothing."),
    ("-0.01", "docs/plan.md:9", "docs/plan.md:9, and docs/plan.md has 5 lines."),
    ("-0.01", "docs/rules.md:3", "docs/rules.md:3, and docs/rules.md does not exist."),
])
def test_a_plans_threshold_is_checked_against_the_line_its_reason_names(rb, threshold, why, refused):
    """Step 6 takes a plan's rule in the plan's words, with its file and line as the reason: rb refuses a threshold that
    line does not state, so a typed threshold never reads as the document's."""
    Path("docs").mkdir()
    Path("docs/plan.md").write_text(PLAN, encoding="utf-8")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    assert rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")[0] == 0
    code, env = rb("criterion", "add", RULE, "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
                   "--at-least", threshold, "--why", why)
    if not refused:
        assert code == 0, env.errors
        return
    assert code == 2 and env.errors[0].code == "E_SOURCE_UNRESOLVED"
    assert env.errors[0].message == f"The criterion's reason is {refused}"
    import rabbit_brain
    assert rabbit_brain.open(rb.cwd).records("criterion") == []
    # the SDK is refused the same way
    with pytest.raises(rabbit_brain.RBError, match="The criterion's reason is"):
        rabbit_brain.open(rb.cwd).experiment("int8").criterion(RULE, metric="top1", subject="int8", reference="fp32",
                                                               at_least=float(threshold), why=why)



ORDERING = "1024 images is the best of 512 and 1024."


def test_an_ordering_a_file_states_is_cited_by_its_words_at_its_line(rb):
    """Review of round 9's fix: a file's ordering went into an unchecked --why, where the opposite of what the line says
    was accepted and no screen showed the file's words. A threshold-0 ordering now takes --source with the file's quote:
    rb checks the words at their line, shows them with file and line, and flags the file when it no longer holds them."""
    Path("docs").mkdir()
    Path("docs/results.md").write_text(f"# Results\n\nWe swept calibration size.\n{ORDERING}\n", encoding="utf-8")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    assert rb("experiment", "add", "Calibration size", "--id", "cal", "--baseline", "c512", "--candidate", "c1024")[0] == 0
    rule = ["criterion", "add", "The claim that 1024 images beats 512 is checked here", "-e", "cal", "--metric", "top1",
            "--subject", "c1024", "--reference", "c512", "--at-least", "0"]
    code, env = rb(*rule, "--source", "docs/results.md", "--quote", "512 images is the best of 512 and 1024.")
    assert code == 2 and env.errors[0].message.startswith("The criterion's source does not hold the ordering it quotes")
    code, env = rb(*rule, "--why", "docs/results.md:4")
    assert code == 2 and '--source docs/results.md --quote "<the line\'s words>"' in env.errors[0].fix
    code, env = rb(*rule, "--source", "docs/results.md", "--quote", ORDERING, "--id", "c-ord")
    assert code == 0, env.errors
    assert env.data["object"]["origin"] == "cited" and env.data["object"]["source"]["resolved"]["line"] == 4
    assert f'cited: docs/results.md:4: "{ORDERING}"' in rb.text("show", "c-ord")
    assert rb("run", "add", "cal", "c512.top1=0.70", "c1024.top1=0.71")[0] == 0
    code, env = rb("show", "c-ord")
    assert not env.data["finding"]["integrity"]
    # the SDK takes the same route
    import rabbit_brain
    c = rabbit_brain.open(rb.cwd).experiment("cal").criterion("The claim again", metric="top1", subject="c1024", reference="c512",
                                                              at_least=0.0, source="docs/results.md", quote=ORDERING)
    assert c.data.origin == "cited" and c.data.source.resolved.line == 4
    Path("docs/results.md").write_text("# Results\n\n512 wins.\n", encoding="utf-8")
    code, env = rb("show", "c-ord")
    assert [p["code"] for p in env.data["finding"]["integrity"]] == ["cited_source_changed"]
    assert env.data["finding"]["integrity"][0]["text"] == "the cited ordering's source: the quote is not in docs/results.md"
    # the protocol and the manual give a file's ordering one route, its quote, and never the unchecked reason
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for said in (flat(START), agents):
        assert 'says ..."' not in said and "a file's line by" in said and "which rb checks at that line" in said


def test_where_the_runs_are_in_a_database_rb_hints_at_reading_a_row_not_typing_one(rb):
    """The Next line after rb criterion add, in a repository that keeps its runs in a database, reads a row: it
    never asks for numbers typed in or a command nobody saw run."""
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    assert rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")[0] == 0
    assert rb("spec", "set", "int8", "seed", "--per-run")[0] == 0
    rule = ["criterion", "add", "INT8 loses at most 0.01 top-1", "-e", "int8", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
            "--at-least", "-0.01"]
    code, env = rb(*rule, "--id", "c-typed")
    assert env.next == ["rb run add int8 fp32.top1=<value> int8.top1=<value> --set seed=<value> --command \"...\""]
    con = sqlite3.connect("results.db")
    con.execute("create table evals (precision text, seed integer, top1 real)")
    con.commit()
    con.close()
    code, env = rb(*rule, "--id", "c-row")
    hint = "rb run add int8 --variant <variant> --from results.db --table <table> --where <column>=<value> --set seed=<value>"
    assert code == 0 and env.next == [hint]
    assert hint in rb.text("status")

def test_the_protocol_and_rb_compile_take_a_plans_threshold_the_same_way(rb):
    contract = " ".join(rb("compile")[1].data["contract"])
    for said in (flat(START), contract):
        assert '--why "<file>:<line>"' in said and "rb checks that the line states" in said


# ---------------------------------------------------------------- the pointer


def test_the_pointer_shows_while_no_question_is_recorded_in_a_repository_with_history(rb):
    git("init", "-q")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    # no commit yet, and a commit of the record alone, are no work already done
    assert pointers(rb("status")[1]) == []
    git("add", ".rb")
    git("commit", "-q", "-m", "start the record")
    assert pointers(rb("status")[1]) == [] and "rb docs --start" not in rb.text("context")
    committed("eval.py", "print('top1')\n")
    item, = pointers(rb("status")[1])
    assert item["who"] == "agent" and item["commits"] == 1 and item["do"].startswith("rb docs --start")
    assert item["what"] == "No question is recorded, and the repository holds work already done: 1 commit"
    text = rb.text("status")
    assert "  - No question is recorded, and the repository holds work already done: 1 commit\n      rb docs --start" in text
    code, env = rb("context")
    assert "rb docs --start" in env.data["markdown"] and env.next[0].startswith("rb docs --start")
    # an experiment with no question is still no question
    assert rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")[0] == 0
    assert len(pointers(rb("status")[1])) == 1
    # once a question is recorded, the pointer goes everywhere
    assert rb("question", "add", "Does INT8 cost top-1 accuracy?")[0] == 0
    assert pointers(rb("status")[1]) == []
    assert "rb docs --start" not in rb.text("status") and "rb docs --start" not in rb.text("context")



OPENING = "## First, record where the work stands"


def test_context_opens_with_recording_where_the_work_stands_where_nothing_is_recorded_yet(rb):
    """The pilot: after the paste line, the agent read rb context, rb docs --start and rb status, said the record was new
    and stopped, recording nothing. The paste line asks for the record; on a record that holds no question and no
    experiment, in a project with work already done, rb context opens with what to do now (record where the work
    stands, from the files, without asking), then the start protocol, whose first lines say to do it now."""
    assert PASTE.endswith(": read rb context, then record where the work stands.") and len(PASTE) < 90
    assert START.splitlines()[2] == "Start it now, in this turn: record where this repository's work stands, from the"
    assert "Ask the researcher nothing." in flat(START.split("\n\n")[1])
    skill = flat((ROOT / "SKILL.md").read_text(encoding="utf-8"))
    assert "record where the work stands now, in the same turn, from the files, and ask nothing first" in skill
    git("init", "-q")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert OPENING not in rb.text("context")                  # no work here yet: nothing to record
    committed("eval.py", "print('top1')\n")
    said = ("Nothing is recorded yet, and this project holds work already done: 1 commit. Record where the work stands now, "
            "in this turn, from the files, and ask the researcher nothing first. How: rb docs --start.")
    assert rb.text("context").splitlines()[:5] == ["# The record: INT8 ResNet-50 on ImageNet", "", OPENING, "", said]
    code, env = rb("context")
    assert code == 0 and env.data["markdown"].startswith(f"# The record: INT8 ResNet-50 on ImageNet\n\n{OPENING}\n\n{said}\n")
    Path("results/int8").mkdir(parents=True)
    Path("results/int8/metrics.json").write_text(json.dumps({"top1": 0.755}), encoding="utf-8")
    assert "holds work already done: 1 commit and 1 results folder. Record where" in rb.text("context")
    # once anything is recorded, the briefing opens with the record again; the start protocol's pointer stays
    assert rb("experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8")[0] == 0
    text = rb.text("context")
    assert OPENING not in text and "rb docs --start" in text


def test_a_new_repositorys_readme_license_and_ignore_files_are_no_work_already_done(rb):
    """A repository made on a hosting service starts with one commit of a README holding its title: rb context opened
    with record where the work stands and rb status pointed at the start protocol, as if work were already done. Its
    README, license and ignore files are a new repository's scaffold; a commit of anything else is work."""
    git("init", "-q")
    Path("README.md").write_text("# INT8 ResNet-50\n", encoding="utf-8")
    Path("LICENSE").write_text("Apache License 2.0\n", encoding="utf-8")
    Path(".gitignore").write_text("out/\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "Initial commit")
    assert rb("init", "INT8 ResNet-50", "--no-agents")[0] == 0
    assert pointers(rb("status")[1]) == [] and OPENING not in rb.text("context")
    committed("docs/README.md", "Calibration uses 512 images.\n")
    [item] = pointers(rb("status")[1])
    assert item["commits"] == 1 and OPENING in rb.text("context")


def test_a_commit_of_what_rb_init_installs_is_no_work_already_done(rb):
    """The researcher runs rb init in a new repository and commits what it wrote, as it tells them to: the pointer
    stays away, since that commit holds no work."""
    git("init", "-q")
    Path(".claude").mkdir()
    Path(".cursor").mkdir()
    Path("AGENTS.md").write_text("# House rules\n", encoding="utf-8")
    assert rb("init", "INT8 ResNet-50 on ImageNet")[0] == 0
    assert Path(".claude/skills/rabbit-brain/SKILL.md").is_file() and Path(".mcp.json").is_file()
    assert Path(".cursor/rules/rabbit-brain.mdc").is_file()
    git("add", "-A")
    git("commit", "-q", "-m", "start the record")
    assert pointers(rb("status")[1]) == [] and "rb docs --start" not in rb.text("context")
    committed("eval.py", "print('top1')\n")
    assert pointers(rb("status")[1])[0]["commits"] == 1

def test_a_database_or_a_results_folder_is_history_too(rb):
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    assert pointers(rb("status")[1]) == []
    Path("results.txt").write_text("not a database\n", encoding="utf-8")
    Path("notes.db").write_text("not a database either\n", encoding="utf-8")
    assert pointers(rb("status")[1]) == []
    con = sqlite3.connect("results.db")
    con.execute("create table evals (precision text, seed integer, top1 real)")
    con.commit()
    con.close()
    item, = pointers(rb("status")[1])
    assert item["stores"] == ["results.db"] and "commits" not in item
    assert item["what"].endswith("work already done: a database (results.db)")
    Path("results/int8").mkdir(parents=True)
    Path("results/int8/metrics.json").write_text(json.dumps({"top1": 0.755}), encoding="utf-8")
    item, = pointers(rb("status")[1])
    assert item["folders"] == ["results/int8/"] and item["what"].endswith("1 results folder and a database (results.db)")


def test_no_record_says_how_to_record_the_work_already_done(rb):
    code, env = rb("status")
    assert code == 2 and env.errors[0].code == "E_NO_PROJECT" and "rb docs --start" in env.errors[0].fix
    code, env = rb("docs", "--errors")
    assert "rb docs --start" in env.data["errors"]["E_NO_PROJECT"]["fix"]
    for argv in (["compile"], ["reconcile"]):
        assert "To record the work already done: rb docs --start." in rb.text(*argv), argv
        assert "rb docs --start" in rb(*argv)[1].next, argv
    Path("results/int8").mkdir(parents=True)
    Path("results/int8/README.md").write_text("# INT8\n\n## Results\n\ntop-1 0.755\n", encoding="utf-8")
    assert "To record the work already done: rb docs --start." in rb.text("reconcile")


# ---------------------------------------------------------------- the paste line, and rb init


def test_the_paste_line_is_the_same_everywhere_it_is_given(rb, workdir):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert PASTE in readme.splitlines()                      # a line of its own, to copy whole
    assert PASTE in (ROOT / "llms.txt").read_text(encoding="utf-8")
    assert f"`{PASTE}`" in (ROOT / "AGENTS.md").read_text(encoding="utf-8") and PASTE in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    code, env = rb("ui")
    assert code == 2 and env.errors[0].code == "E_NO_PROJECT" and env.errors[0].message.endswith(PASTE)
    text = rb.text("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    assert text.rstrip("\n").endswith(f"Then paste this line into your agent's chat:\n  {PASTE}") and "Next:" not in text
    assert home(workdir).quiet == f"Nothing recorded yet. To start, paste this line into your agent's chat: {PASTE}"


def test_init_says_when_gitignore_keeps_what_it_installs_out_of_git(rb):
    git("init", "-q")
    Path(".claude").mkdir()
    Path(".gitignore").write_text(".claude/\n", encoding="utf-8")
    code, env = rb("init", "INT8 ResNet-50 on ImageNet")
    assert code == 0 and env.data["paste"] == PASTE
    agents = env.data["agents"]
    assert agents["ignored"] == [".claude/skills/rabbit-brain/SKILL.md"] and agents["later"] == []
    assert (".gitignore keeps .claude/skills/rabbit-brain/SKILL.md out of git, so it does not travel with the repository: "
            "a clone, and the agents working in it, will not have it.") in agents["said"]
    code, env = rb("skill", "add", "--claude", "--project")
    assert env.data["ignored"] == [".claude/skills/rabbit-brain/SKILL.md"]
    Path(".gitignore").write_text("", encoding="utf-8")
    code, env = rb("skill", "add", "--claude", "--project")
    assert env.data["ignored"] == []


@pytest.mark.parametrize("runtime, sign, later", [
    ({"CLAUDECODE": "1"}, ".claude", "Claude Code: rb skill add --claude --project, and claude mcp add --scope project rabbit-brain -- rb mcp"),
    ({"CODEX_THREAD_ID": "thread-int8-1"}, "AGENTS.md", "Codex and the agents that read AGENTS.md: rb skill add --agents-md"),
    ({"CURSOR_AGENT": "1"}, ".cursor", "Cursor: rb skill add --cursor"),
    ({"CURSOR_CONVERSATION_ID": "cv-int8-1"}, ".cursor", "Cursor: rb skill add --cursor"),      # the chat of Cursor's agent
    ({"CLAUDE_CODE_SESSION_ID": "sess-int8-1"}, ".claude",
     "Claude Code: rb skill add --claude --project, and claude mcp add --scope project rabbit-brain -- rb mcp"),
])
def test_init_in_an_agent_session_installs_nothing_that_session_would_not_load(rb, monkeypatch, runtime, sign, later):
    """A running session loads no skill or MCP server added during it: rb init installs nothing, names the commands
    for later sessions, and gives the session the paste line's instruction. rb reconcile --apply, which starts a record
    too, does the same."""
    if sign == "AGENTS.md":
        Path(sign).write_text("# House rules\n", encoding="utf-8")
    else:
        Path(sign).mkdir()
    before = sorted(str(p) for p in Path(".").rglob("*"))
    for k, v in runtime.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("RB_SESSION", "sess-init-1")
    code, env = rb("init", "INT8 ResNet-50 on ImageNet")
    assert code == 0 and env.data["agents"]["written"] == [] and env.data["agents"]["later"] == [later]
    assert env.data["agents"]["ignored"] == []                  # the same keys on every path
    assert sorted(str(p) for p in Path(".").rglob("*") if ".rb" not in p.parts and p.name != ".gitattributes") == before
    assert env.next == ["rb context --model <your model id>"]
    if sign == "AGENTS.md":
        assert Path(sign).read_text(encoding="utf-8") == "# House rules\n"



def test_init_in_a_ci_job_installs_as_at_the_researchers_terminal(rb, monkeypatch):
    """A CI job is no chat session: rb init installs the skill and the MCP entry, and says to paste the line."""
    Path(".claude").mkdir()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "run-int8-1")
    code, env = rb("init", "INT8 ResNet-50 on ImageNet")
    assert code == 0 and ".mcp.json" in " ".join(env.data["agents"]["written"])
    assert Path(".claude/skills/rabbit-brain/SKILL.md").is_file() and Path(".mcp.json").is_file()
    Path(".rb").rename(".rb-first")
    assert rb.text("init", "INT8 ResNet-50 on ImageNet").rstrip("\n").endswith(f"Then paste this line into your agent's chat:\n  {PASTE}")

def test_init_text_in_an_agent_session_is_the_paste_line_and_the_later_commands(rb, monkeypatch):
    Path(".claude").mkdir()
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "sess-init-2")
    text = rb.text("init", "INT8 ResNet-50 on ImageNet")
    assert ("For the agents here:\n"
            "  Nothing installed: a session already running loads no skill or MCP server added during it. For later sessions:\n"
            "    Claude Code: rb skill add --claude --project, and claude mcp add --scope project rabbit-brain -- rb mcp\n"
            f"\n{PASTE}\n\nNext: rb context --model <your model id>") in text
    assert not Path(".claude/skills").exists() and not Path(".mcp.json").exists()


def test_reconcile_apply_in_an_agent_session_installs_nothing_either(rb, monkeypatch):
    git("init", "-q")
    Path(".claude").mkdir()
    committed("README.md", "# INT8 ResNet-50\n")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("RB_SESSION", "sess-init-3")
    code, env = rb("reconcile", "--apply")
    assert code == 0 and env.data["agents"]["written"] == [] and not Path(".mcp.json").exists()


def test_the_protocol_checks_a_claims_number_and_leaves_an_unstated_direction_uncomputed():
    """Review of group 9: a memory claim that carried a number had no route (its number is no threshold a file states,
    and a session's own query checks nothing); the cold session found no file stating the direction of the frontier's
    own comparison and borrowed a sentence that named no stage; the claim's line had nowhere to go; and a tolerance was
    taken from one rounded figure where the table under it held the figures it is computed from."""
    said = flat(START)
    for rule in ("A claim's number is its rule, with no threshold of your own",
                 "its line goes in a note on the experiment",
                 "(one that names none is about the runs its figures come from)",
                 "With no file stating the direction of the frontier's own comparison and no next step stated for when it holds (step 8), it "
                 "takes no rule, and the report says rb does not compute it yet",
                 "read those figures in the table it sits under"):
        assert rule in said, rule
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("a claim's number is its rule, with no threshold of the session's own",
                 "no premise no file states, its line in a note on the experiment",
                 "with no file stating the direction of the frontier's own comparison and no next step stated for when it holds, it takes no rule",
                 "read in the table it sits under"):
        assert rule in agents, rule


def test_a_stated_next_step_gives_its_rule_a_direction_and_a_claim_check_waits_for_a_call():
    """Round 8: step 7's last sentence overrode its own exception for a next step stated in the chat, so both memory
    trials set the gate with no rule under it and rb never computed the comparison it rests on; a claim check declared
    exploratory missed and waited for no one; notes said "as recalled" and "as believed", and reports "your recalled
    words"; a claim check's statement put the remembered number as a fact; and a cold question kept one of the two
    factors the files' claim names."""
    said = flat(START)
    for rule in ("With no file stating the direction of the frontier's own comparison and no next step stated for when it holds (step 8), it "
                 "takes no rule",
                 "Their words give the direction of the comparison the gate rests on: a direction rule on it (rb criterion add ... --better, "
                 "no threshold, those words, as the gate takes them), so rb computes it",
                 "A claim check takes the default role or secondary, never exploratory, so a miss waits under Needs judgment",
                 "The rule's statement words the claim it checks (\"the claim that INT8 ends at 0.755 top-1 is checked here\"), never the "
                 "number as a fact",
                 "A note names no one and says no memory: no \"as recalled\", \"as believed\" or \"remembered\"",
                 "never \"your words\" in any form (not \"your recalled words\")",
                 "the question is the files' own claim turned into a question, with every factor it names",
                 "(\"calibration size matters more than bit width\" asks whether it does, not about calibration size alone)"):
        assert rule in said, rule
    assert "With no file stating the direction of the frontier's own comparison, it takes no rule" not in said
    assert "or quote them as recalled" not in said
    # step 8's rule comes after the exception it rests on, and nothing after it takes it back
    assert said.index("a next step stated in this chat") < said.index("no next step stated for when it holds") < said.index("Their words give the direction")
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("with no file stating the direction of the frontier's own comparison and no next step stated for when it holds, it takes no rule",
                 "its words give the direction of the comparison the gate rests on: a direction rule on it (`--better`, no threshold, those "
                 "words, as the gate takes them), so rb computes it",
                 "a claim check takes the default role or secondary, never exploratory, so a miss waits under Needs judgment",
                 "the rule's statement words the claim it checks, never the number as a fact",
                 "says no memory (no \"as recalled\", \"as believed\" or \"remembered\")",
                 "never \"your words\" in any form (not \"your recalled words\")",
                 "the question is the files' own claim turned into a question, with every factor it names"):
        assert rule in agents, rule


def test_only_a_next_step_for_when_it_holds_sets_a_gate_and_a_claims_line_stays_in_its_words():
    """Review of group 10: step 8 made any next step a file states a gate whose words give a direction, so a cold
    session took a script's "fill to three seeds" as the gate and a direction rule no file states; step 4 left open
    whether a claim's line is its --words or its --why and read "sets no threshold" as forbidding --equals; and step 3
    put the question's note before step 6's first rb status."""
    said = flat(START)
    for rule in ("A next step the researcher stated for when the comparison holds (\"if it holds, we ...\")",
                 "a plan's step with no such condition sets no gate",
                 "With no file stating the direction of the frontier's own comparison and no next step stated for when it holds (step 8), it "
                 "takes no rule",
                 "the researcher's words memory quotes in --words (step 12), memory's own claim in the statement, a file's line by --source <file> --quote \"<its words>\", never in --why",
                 "A claim's number is its rule, with no threshold of your own: --equals 0.755 --tolerance 0.0005, half its last digit (step 7); "
                 "a bound (\"x or less\") is --at-most x, a spread (\"differ by x or less\") --equals 0 --tolerance x",
                 "its line goes in a note on the experiment, after the first rb status (step 6)"):
        assert rule in said, rule
    for gone in ("in words its reason", "it sets no threshold.", "With no next step stated and", "or in a file, if it holds, is the gate"):
        assert gone not in said, gone
    # the question's note comes after the standing, as every note does
    assert said.index("Then run rb status, before any note") > said.index("after the first rb status (step 6)")
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("and no gate from a plan's step with no such condition", "the researcher's words memory quotes in `--words` and memory's own claim in the statement, a file's line by `--source <file> --quote \"<its words>\"`, never in `--why`",
                 "`--equals` the number, `--tolerance` half its last digit; a bound",
                 "its line in a note on the experiment after the first `rb status`"):
        assert rule in agents, rule


def test_the_protocol_gives_each_voice_its_own_and_reads_a_bound_as_a_bound():
    """Review of round 9's fix: "as you said earlier" read as covering memory's own working figures; a file's next step
    went in --words, which rb shows as the researcher's own words once confirmed; "0.15 or less" read literally became
    --equals 0.15 and three false misses; step 5 left out the frontier's own rows step 3 records; the report quoted the
    recorded question, so it held a question mark; and the rounding example used figures from a real project."""
    said = flat(START)
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("Say the researcher's words memory quotes \"as you said earlier\" (the gate on <exp>, as you said earlier: \"<their words>\"), "
                 "memory's own claims with no speaker, a file's words with <file>:<line>",
                 "--words is only the researcher's words memory quotes; memory's own claims stay in the statement",
                 "and the whole sentence goes in --words (a file's: --why \"<file>:<line>\")",
                 "a bound (\"x or less\") is --at-most x, a spread (\"differ by x or less\") --equals 0 --tolerance x",
                 "then only the runs those rules read and the frontier's own rows (step 3)",
                 "what you took as the current frontier, by its question id, and why",
                 "(-0.006 as 0.755 - 0.761 takes --tolerance 0.001)"):
        assert rule in said, rule
    for rule in ("the researcher's words memory quotes \"as you said earlier\" (the gate on `<exp>`, as you said earlier: \"<their words>\"), "
                 "memory's own claims with no speaker, a file's words with `<file>:<line>`",
                 "a file's as `--why \"<file>:<line>\"` with no `--words`",
                 "a bound (\"x or less\") is `--at-most x`, a spread (\"differ by x or less\") `--equals 0 --tolerance x`",
                 "and only the runs they read, with the frontier's own rows",
                 "what it took as the current frontier, by its question id, and why",
                 "(a paper's -0.006 worked out as 0.755 - 0.761 from two rounded means takes `--tolerance 0.001`)"):
        assert rule in agents, rule
    for gone in ("Say memory's words", "the claim in --words", "recalled words go in --words"):
        assert gone not in said and gone not in agents, gone


def test_the_protocol_says_memory_as_said_checks_a_files_own_claim_and_reads_every_factor():
    """Round 9: a report said "in your words from earlier", "your rule" and "your call" for a launch rb lists as no
    call; a session checked a paper's statement about means with --better, a stronger claim than the paper's, and put
    the line in a paraphrased reason; set a past run's settings from lines younger than the run; passed the whole
    conditional sentence as --if-holds; and a cold session recorded no runs for one of its question's two factors."""
    said = flat(START)
    for rule in ("the gate on <exp>, as you said earlier: \"<their words>\")",
                 "and never \"your rule\", \"in your words from earlier\" or \"your call\" for what rb does not list as the researcher's call",
                 "A file's claim is checked only where the frontier rests on it, by the file's own comparison, never a stronger one: a statement "
                 "about means (\"1024 images is the best of 256, 512 and 1024\") is --at-least 0 per pair (--at-most 0 where lower is better), read on the estimate, not --better, "
                 "which needs the interval clear of 0; its line is --source <file> --quote \"<its words>\", which rb checks at that line",
                 "A setting of a past run is set only from that run's row, its config or a file at a commit before the run; otherwise leave it "
                 "out, with a note.",
                 "--if-holds \"<the action>\" --words \"<their words>\": --if-holds is what they would do (\"we ship INT8\"), and the whole "
                 "sentence goes in --words",
                 "Then record the runs at the frontier's setting that every factor of that question needs (for bit width and calibration size, "
                 "the rows at each calibration size the files name), so a rule reads each factor"):
        assert rule in said, rule
    assert '--if-holds "<their words>"' not in said
    # the cold runs come after the cold question, and the settings sentence where the settings are set
    assert said.index("with every factor it names") < said.index("every factor of that question needs")
    assert said.index("Set the shared settings before the runs") < said.index("A setting of a past run") < said.index("Then run rb status")
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("the gate on `<exp>`, as you said earlier: \"<their words>\")",
                 "and never \"your rule\", \"in your words from earlier\" or \"your call\" for what rb does not list as the researcher's call",
                 "a file's claim is checked only where the frontier rests on it, by the file's own comparison, never a stronger one",
                 "a setting of a past run is set only from that run's row, its config or a file at a commit before the run, else left out with a note",
                 "`--if-holds` is what they would do, and the whole sentence goes in `--words`",
                 "the runs at the frontier's setting that every factor of that question needs are recorded, so a rule reads each factor"):
        assert rule in agents, rule
    assert '--if-holds "<their words>"`' not in agents


def test_the_protocol_cites_a_why_checks_memorys_own_number_and_says_a_miss_in_rbs_words():
    """Round 10: a session's experiment --why restated a file's ordering as fact; memory's 0.93 was called a different
    number from rows that give 0.928; a run's setting came from a sweep's metadata table written after the runs; the
    first rb status ran before the rules the frontier rests on; a cold session put no rule on one factor its question
    names though a file states that comparison; and a report said a miss "contradicts". Each is one sentence, in the step
    it belongs to, in the protocol and in the manual's summary."""
    said = flat(START)
    for rule in ("A --why, where a file is the reason, cites the file and its line;", "A --why never restates a claim as fact.",
                 "Before calling memory's number different from a file's, check it with its own --equals rule (step 4): a file's rounding is not a difference.",
                 "A summary table written after the runs (a sweep's metadata table) is not a pre-run source for a run's setting.",
                 "The rules the frontier rests on are in before the first rb status.",
                 "Where a file states the comparison for a factor the question names, that factor gets a rule (step 7).",
                 "A miss is said in rb's words (\"not reproduced\", \"not met\"), never \"contradicts\"."):
        assert rule in said, rule
    # each in its step: the cold rule after the cold runs, the settings sentence where settings are set, the rules
    # before the first status, the memory check where a difference is noted, the why with the session's own text
    assert said.index("so a rule reads each factor") < said.index("that factor gets a rule (step 7)") < said.index("4. Check each claim")
    assert said.index("A setting of a past run") < said.index("A summary table written after the runs") < said.index("Then run rb status")
    assert said.index("The rules the frontier rests on are in before") < said.index("Then run rb status") < said.index("7. Take criteria")
    assert said.index("Where memory and a file differ") < said.index("Before calling memory's number different") < said.index("12. Never name")
    assert said.index("12. Never name") < said.index("A --why, where a file is the reason") < said.index("Then report to the researcher")
    assert said.index("Then report to the researcher") < said.index("A miss is said in rb's words")
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    for rule in ("and a `--why`, where a file is the reason, cites the file and its line", "and a `--why` never restates a claim as fact",
                 "once memory's number is checked with its own `--equals` rule, since a file's rounding is not a difference",
                 "a summary table written after the runs (a sweep's metadata table) is not a pre-run source for a run's setting",
                 "the rules the frontier rests on are in before the first `rb status`",
                 "where a file states the comparison for a factor the question names, that factor gets a rule",
                 "a miss in rb's words (\"not reproduced\", \"not met\"), never \"contradicts\""):
        assert rule in agents, rule


def test_the_protocol_puts_claim_checks_before_the_first_status_and_reports_the_snapshot_ref():
    """Review of round 10's fix: step 4 put memory's claim checks "right after the first rb status" while step 6 put the
    rules the frontier rests on before it; step 4 put "memory's words" in --words against step 12; "A --why cites a file
    and its line" left no way for a rule or gate whose words come from a next step stated in the chat; and only the
    session saw where rb put the code in git. Step 4 checks before the first status, --words holds the researcher's
    words memory quotes, a next step's rule or gate takes --words and no --why, and the report says where the ref is."""
    said = flat(START)
    assert "right after the first rb status" not in said and "memory's words in --words" not in said
    assert said.index("4. Check each claim") < said.index("before the first rb status (step 6)") < said.index("5. Record the frontier")
    assert ("a rule or gate from a next step stated in this chat takes its words in --words and no --why. "
            "A --why never restates a claim as fact.") in said
    assert said.index("12. Never name") < said.index("a rule or gate from a next step stated in this chat") < said.index("Then report to the researcher")
    bullet = "- when a write said rb put the code in git (refs/rb/snapshots/<tree>), where it is and the command that removes it;"
    assert said.index("Then report to the researcher") < said.index(bullet) < said.index("- what you left out.")
    agents = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert "right after the first `rb status`" not in agents and "and now, before the first `rb status`:" in agents
    for rule in ("a rule or gate from a next step stated in the chat takes its words in `--words` and no `--why`",
                 "when a write said rb put the code in git (`refs/rb/snapshots/<tree>`), where it is and the command that removes it"):
        assert rule in agents, rule


# ---------------------------------------------------------------- a clone's start: what it takes to rerun the parent


def a_published_parent(rb, monkeypatch, tmp_path_factory, *, repo=True, e2_command=None):
    """ana's INT8 ResNet-50 record, committed with its code (eval.py) and one run of e1 made by a command, pushed to a
    bare repository standing in for the code's address, and exported with the record. Returns the bundle's path, and
    the address and commit the bundle names (None, None with --no-repo)."""
    import os
    import sys
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.environ["GIT_CONFIG_GLOBAL"])
    git("init", "-q", "-b", "main")
    Path("eval.py").write_text("import json, os, sys\nos.makedirs(sys.argv[1], exist_ok=True)\n"
                               "json.dump({'fp32': {'top1': 0.7613}, 'int8': {'top1': 0.7581}}, open(os.path.join(sys.argv[1], 'metrics.json'), 'w'))\n",
                               encoding="utf-8")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--id", "int8-resnet-50", "--no-agents")[0] == 0
    assert rb("question", "add", "Is INT8 ResNet-50 as accurate as FP32 on ImageNet?", "--id", "q1")[0] == 0
    assert rb("experiment", "add", "INT8 against FP32: top-1 on the validation set", "--id", "e1", "-q", "q1",
              "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")[0] == 0
    assert rb("metric", "add", "top1", "--maximize")[0] == 0
    assert rb("resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation",
              "--recipe", "python get_data.py --split val")[0] == 0
    assert rb("criterion", "add", "INT8 loses at most a point of top-1", "-e", "e1", "-q", "q1", "--metric",
              "change.top1", "--at-least", "-0.01", "--id", "c1")[0] == 0
    git("add", "-A")
    git("commit", "-qm", "eval.py and the record")
    subprocess.run([sys.executable, "eval.py", "out/s1"], check=True)
    assert rb("run", "add", "e1", "--from", "out/s1/metrics.json", "--command", "python eval.py out/s1",
              "--input", "imagenet-val:eval")[0] == 0
    if e2_command:
        # e2, per-channel INT8, measured by a command of its own (or by e1's)
        assert rb("experiment", "add", "Per-channel INT8: top-1 on the validation set", "--id", "e2", "-q", "q1")[0] == 0
        assert rb("criterion", "add", "Per-channel INT8 keeps 75 top-1", "-e", "e2", "-q", "q1", "--metric", "top1",
                  "--at-least", "0.75", "--id", "c2")[0] == 0
        assert rb("run", "add", "e2", "top1=0.7598", "--command", e2_command)[0] == 0
    git("add", "-A")
    git("commit", "-qm", "seed 1")
    bare = tmp_path_factory.mktemp("published") / "int8.git"
    git("clone", "-q", "--bare", ".", str(bare))
    git("remote", "add", "origin", served_at(monkeypatch, bare))
    commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    code, env = rb("export", "--with-record", *([] if repo else ["--no-repo"]), "--out", str(tmp_path_factory.mktemp("bundle") / "b"))
    assert code == 0, env.errors
    return Path(env.data["path"]), (bare, commit) if repo else (None, None)


def test_a_clone_s_context_and_status_open_with_what_it_takes_to_rerun_the_parent(rb, monkeypatch, tmp_path_factory):
    bundle, (bare, commit) = a_published_parent(rb, monkeypatch, tmp_path_factory)
    fresh = tmp_path_factory.mktemp("reader")
    monkeypatch.chdir(fresh)
    assert rb("clone", str(bundle), "int8-rerun")[0] == 0
    monkeypatch.chdir(fresh / "int8-rerun")
    first = "rb journal run -- python eval.py out/s1"
    # rb context: the block comes before the rules and the questions, and names the four things in their order
    code, env = rb("context")
    assert code == 0
    md = env.data["markdown"]
    block = md.split("## Rerun the parent\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert md.index("## Rerun the parent") < md.index("## Question q1")
    assert [line.split(":", 1)[0] for line in block] == ["- parent", "- code", "- resource imagenet-val (ImageNet-1k validation)", "- e1, first, for c1"]
    assert f"cloned {env.data['rerun']['parent']['cloned_at'][:10]} from {bundle.as_posix()}" in block[0]
    assert env.data["rerun"]["code"]["commit"] == commit and env.data["rerun"]["code"]["state"] == "here"
    assert "how to get it again: python get_data.py --split val" in block[2]
    assert block[3].startswith(f"- e1, first, for c1: `{first}`") and block[3].endswith("; no run recorded here yet")
    assert env.next[0] == first and env.data["rerun"]["experiments"][0]["first"] == first
    # rb status says the same block under the title, and the same first step
    said = rb.text("status")
    lines = said.splitlines()
    at = lines.index("Rerun the parent")
    assert lines[at + 1:at + 1 + len(block)] == [f"  {line}" for line in block]
    assert at < next(i for i, line in enumerate(lines) if line.startswith("Rules:") or line.startswith("Questions"))
    code, env = rb("status")
    assert env.next[0] == first and env.data["rerun"]["code"]["state"] == "here"


def test_a_clone_with_no_code_puts_getting_the_code_first(rb, monkeypatch, tmp_path_factory):
    bundle, _ = a_published_parent(rb, monkeypatch, tmp_path_factory, repo=False)
    fresh = tmp_path_factory.mktemp("reader")
    monkeypatch.chdir(fresh)
    assert rb("clone", str(bundle), "r")[0] == 0
    monkeypatch.chdir(fresh / "r")
    get = f"rb clone {bundle.as_posix()} <new folder> --code <address of the code>"
    for cmd in ("context", "status"):
        code, env = rb(cmd)
        assert code == 0 and env.next[0] == get and not any(n.startswith("rb journal run") for n in env.next), (cmd, env.next)
    code, env = rb("context")
    assert f"- code: the runs need the code; this bundle doesn't say where it is: `{get}`" in env.data["markdown"].splitlines()


def test_a_record_that_is_no_clone_says_nothing_of_a_rerun(rb):
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")[0] == 0
    for cmd in ("context", "status"):
        code, env = rb(cmd)
        assert code == 0 and "rerun" not in env.data
    assert "Rerun the parent" not in rb.text("context") and "Rerun the parent" not in rb.text("status")


def test_a_clone_s_next_says_a_command_two_experiments_share_once(rb, monkeypatch, tmp_path_factory):
    bundle, _ = a_published_parent(rb, monkeypatch, tmp_path_factory, e2_command="python eval.py out/s1")
    fresh = tmp_path_factory.mktemp("reader")
    monkeypatch.chdir(fresh)
    assert rb("clone", str(bundle), "r")[0] == 0
    monkeypatch.chdir(fresh / "r")
    first = "rb journal run -- python eval.py out/s1"
    for cmd in ("context", "status"):
        code, env = rb(cmd)
        assert [e["first"] for e in env.data["rerun"]["experiments"]] == [first, first]
        assert env.next.count(first) == 1, (cmd, env.next)


def test_rb_status_of_one_experiment_in_a_clone_gives_that_experiment_s_command(rb, monkeypatch, tmp_path_factory):
    bundle, _ = a_published_parent(rb, monkeypatch, tmp_path_factory, e2_command="python eval.py --per-channel out/s2")
    fresh = tmp_path_factory.mktemp("reader")
    monkeypatch.chdir(fresh)
    assert rb("clone", str(bundle), "r")[0] == 0
    monkeypatch.chdir(fresh / "r")
    e1, e2 = "rb journal run -- python eval.py out/s1", "rb journal run -- python eval.py --per-channel out/s2"
    code, env = rb("status")
    assert env.next[:2] == [e1, e2]
    code, env = rb("status", "e2")
    assert env.next[0] == e2 and e1 not in env.next
    # the block still says the whole parent: the rerun is the project's
    assert [e["id"] for e in env.data["rerun"]["experiments"]] == ["e1", "e2"]


def _ok(rb, *argv) -> Envelope:
    code, env = rb(*argv)
    assert code == 0, (argv, env.errors)
    return env


def _edited(rb) -> list:
    """What rb status reads as changed outside rb."""
    return [i for i in _ok(rb, "status").data["open"] if i["code"] == "edited_outside_rb"]


def test_a_wall_clock_stepped_back_never_reads_rbs_own_write_as_edited(rb, monkeypatch):
    """The log is read in the order of its times. A clock stepped back between two writes of one record (an NTP or a
    virtual machine's correction) would date the second before the first, and the record would read as not what rb
    last wrote, refusing the next write. rb dates a write no earlier than the last write of each record it writes, so
    the record stays rb's, and a record no earlier line wrote keeps the clock's time."""
    rule = ("--metric", "change.top1", "--at-least", "-0.5")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00.000+00:00")
    _ok(rb, "init", "INT8 ResNet-50", "--no-agents")
    for e in ("e1", "e2"):
        _ok(rb, "experiment", "add", f"INT8 vs FP32 on ImageNet, {e}", "--id", e, "--baseline", "fp32", "--candidate", "int8")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00.500+00:00")
    _ok(rb, "criterion", "add", "INT8 costs at most 0.5 top-1 points", "-e", "e1", *rule, "--id", "c1")
    _ok(rb, "criterion", "add", "INT8 costs at most 0.5 top-1 points", "-e", "e2", *rule, "--id", "c2")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00.400+00:00")   # the clock steps back 100 ms
    _ok(rb, "criterion", "relate", "c1", "reverses", "c2", "--orientation", "same", "--why", "the same comparison at two budgets")
    _ok(rb, "note", "add", "the held-out set is ImageNet's validation split", "-e", "e1")
    monkeypatch.setenv("RB_TEST_NOW", "2026-10-01T12:00:00.600+00:00")
    _ok(rb, "criterion", "relate", "c1", "reverses", "c2", "--remove", "--why", "withdrawn")
    assert _edited(rb) == []
    lines = [json.loads(line) for line in (rb.cwd / ".rb" / "log.jsonl").read_text().splitlines() if line.strip()]
    relates = [x["at"] for x in lines if x.get("id") == "c1" and x["op"] != "add"]
    assert relates == ["2026-10-01T12:00:00.500+00:00", "2026-10-01T12:00:00.600+00:00"], relates
    # a record no earlier line wrote keeps the clock's time
    assert [x["at"] for x in lines if x["op"] == "note"] == ["2026-10-01T12:00:00.400+00:00"]


SAYS_OUT_OF_ORDER = ("experiment e1 is what rb wrote in a later line of log.jsonl that is dated before the line rb reads as its "
                     "last (a clock stepped back, or a merge or rebase put the lines out of order): rb doctor --adopt keeps it as "
                     "it stands where that changes no one's authorship, and rb doctor --restore puts back the write dated last, "
                     "after which the later change is made again through rb")


def _date_before_the_add(rb, op: str) -> None:
    """Dates e1's line of `op` before the line that added e1, as an earlier rb wrote it while the clock stepped back."""
    log = rb.cwd / ".rb" / "log.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    added = next(r for r in rows if r.get("id") == "e1")
    later = next(r for r in rows if r.get("op") == op and r.get("id") == "e1")
    later["at"] = "2026-01-01T00:00:00.000+00:00"
    assert later["at"] < added["at"]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _int8(rb) -> None:
    _ok(rb, "init", "INT8 ResNet-50", "--no-agents")
    _ok(rb, "experiment", "add", "INT8 vs FP32 on ImageNet", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")


def test_a_log_that_already_holds_a_stepped_back_line_says_so_and_the_user_keeps_it(rb):
    """A log an earlier rb wrote while the clock stepped back holds a line dated before the one it follows, and a merge
    or a rebase can put lines so too: rb cannot tell which, so the record still reads as not what rb last wrote, and
    rb says what it is and the ways out. Where keeping it changes no one's authorship (a gate), the user adopts it, and
    the next write goes in."""
    _int8(rb)
    _ok(rb, "experiment", "gate", "e1", "--if-holds", "INT8 ships")
    _date_before_the_add(rb, "gate")
    [item] = _edited(rb)
    assert SAYS_OUT_OF_ORDER in item["what"], item
    _ok(rb, "doctor", "--adopt", "--why", "the clock stepped back")
    assert _edited(rb) == []
    _ok(rb, "experiment", "gate", "e1", "--if-holds", "INT8 ships", "--if-not", "FP32 stays")
    assert _edited(rb) == []


def test_a_stepped_back_freeze_is_never_adopted_past_the_write_dated_last(rb):
    """Keeping a record whose write is dated before its last would skip what the write dated last made, and that may be
    a later freeze or call (a merge's stale side): the adoption is checked against the write dated last, so a freeze it
    would change is refused. The user restores the write dated last, and freezes again."""
    _int8(rb)
    _ok(rb, "criterion", "add", "INT8 costs at most 0.5 top-1 points", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.5", "--id", "c1")
    _ok(rb, "freeze", "e1", "-m", "before the runs")
    _date_before_the_add(rb, "freeze")
    [item] = _edited(rb)
    assert SAYS_OUT_OF_ORDER in item["what"], item
    code, env = rb("doctor", "--adopt", "--why", "the clock stepped back")
    assert code == 2 and env.errors[0].code == "E_ADOPT_AUTHORITY", env.errors
    _ok(rb, "doctor", "--restore")
    assert _edited(rb) == []
    _ok(rb, "freeze", "e1", "-m", "before the runs")
    assert _edited(rb) == [] and _ok(rb, "show", "e1").data["object"].get("frozen")
