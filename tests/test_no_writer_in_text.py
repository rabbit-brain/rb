"""Nothing anyone reads says who made a write.

rb's text (what every command prints, the briefing, rb_delta's lines) and every sentence rb builds, also where it
travels inside --json or MCP data, name no writer, a human or an agent: no name or id, no agent session, no channel
(via), nothing of how rb knew (detected:), and no branch an agent's work sat on but in the label rb computes for a
place (an Elsewhere line of rb status), which names the place by its branch as git gives it and adds no word that ties
it to a writer. A human's name beside an agent's missing one would give the agent away, so nobody is named. A call keeps its verb, its date and its reason. rb doctor's
identity check alone says how this shell's writes are recorded.

The back end keeps all of it: the record, the fields of --json and MCP, and the SDK. Every name, session id and branch
here is made up (tests/rbtest.py: writers_record).
"""
import json
import re
import shutil

import pytest

from rbtest import (AGENT_BRANCHES, CLAUDE_CODE, CODEX, MCP_CLIENT, MCP_SESSION, WRITER_FIELDS, Mcp, Writers, as_the_user, home, main,
                    object_card, shown_strings, state, writer_marks, writers_record)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record is kept in a git repository")

HUMAN = "human:writer-ana"
READERS = {"the user": Writers.HUMAN, "Claude Code": CLAUDE_CODE, "Codex": CODEX}


@pytest.fixture(scope="module")
def rec(tmp_path_factory):
    return writers_record(tmp_path_factory.mktemp("writers") / "work")


def identity_check(line: str) -> bool:
    """rb doctor's identity check: how this shell's writes are recorded, the one line of rb's text that says it."""
    return line.startswith("ok   actor: ")


def place_label(line: str) -> str:
    """The label rb computes for a place at the head of an Elsewhere line (its branch, and a worktree's path, at its
    commit), or "" for any other line."""
    m = re.match(r"^  (\S+)( · .+?)? at [0-9a-f]{12}( \(removed since\))?: ", line)
    return m.group(0) if m else ""


def marks_in_text(text: str) -> list:
    """What says who wrote, line by line, but in a place's own label: its branch is said verbatim, whatever it names."""
    found = []
    for line in text.splitlines():
        label = place_label(line)
        marks = [m for m in writer_marks(line) if not (m in label and m not in line[len(label):])]
        if marks and not identity_check(line):
            found.append((line, marks))
    return found


def marks_in_json(envelope: dict, doctor: bool = False) -> list:
    if doctor:
        envelope = {**envelope, "data": {**envelope["data"], "checks": [c for c in envelope["data"]["checks"] if c["check"] != "actor"]}}
    return [(path, text, writer_marks(text)) for path, text in shown_strings(envelope) if writer_marks(text)]


def every_id(rec) -> list:
    """Each record here by the id rb show takes, and each setting by its address."""
    rb_dir = rec.root / ".rb"
    ids = [p.stem for kind in ("questions", "metrics", "resources", "decisions") for p in sorted((rb_dir / kind).glob("*.yaml"))]
    ids += [p.parent.name for p in sorted(rb_dir.glob("experiments/*/experiment.yaml"))]
    ids += [p.stem for kind in ("criteria", "runs", "analyses") for p in sorted(rb_dir.glob(f"experiments/*/{kind}/*.yaml"))]
    return [*ids, "e1/cuda", "e1/data.workers", "e1/dropout", "e1/width", "e1/optim.lr"]


def test_no_write_prints_who_made_it(rec):
    """What each write printed, a human's or an agent's, says what it did and never who did it. In an agent session
    an RB_ACTOR is said to be ignored, with no name and no runtime."""
    leaks = [(argv, found) for argv, text in rec.said for found in marks_in_text(text)]
    assert not leaks, leaks
    [ignored] = [text for argv, text in rec.said if argv.startswith("note add typed in")]
    assert "· RB_ACTOR is ignored in an agent session" in ignored
    # a write rb refuses says why in the same words: the declaration in the way, when, and not by whom
    back = ("criterion", "relate", "c2", "reverses", "c1", "--orientation", "opposite", "--why", "read it the other way")
    refused = rec.run(Writers.HUMAN, *back)
    assert refused.returncode == 2 and "c1 already declares that it reverses c2 with orientation same, declared at " in refused.stderr
    assert not marks_in_text(refused.stderr) and not marks_in_json(json.loads(rec.run(Writers.HUMAN, *back, "--json").stdout))
    for name, result in rec.mcp_writes:
        assert not marks_in_json(result["structuredContent"]), (name, marks_in_json(result["structuredContent"]))
        text = result["content"][0]["text"]
        assert not (marks_in_text(text) if name == "context" else marks_in_json(json.loads(text))), (name, text)


@pytest.mark.parametrize("reader", READERS)
def test_no_read_prints_who_made_a_write(rec, reader):
    """rb status, rb context, rb log, rb compare, rb journal, rb doctor and rb show of every record, in text and in
    every sentence --json carries, read the same for the user and in each agent's session (with its rb_delta)."""
    who = READERS[reader]
    reads = [("status",), ("context",), ("log", "-n", "500"), ("compare", "e1"), ("journal",), ("doctor",)]
    reads += [("show", i) for i in (every_id(rec) if reader == "the user" else ["c2", "q2", rec.ids["run_claude"], rec.ids["investigating"]])]
    leaks = []
    for argv in reads:
        leaks += [(argv, found) for found in marks_in_text(rec.rb(who, *argv))]
        leaks += [(argv, found) for found in marks_in_json(rec.json(who, *argv), doctor=argv == ("doctor",))]
    assert not leaks, leaks


def without_writer_fields(node, parent: str = ""):
    """`node` without the fields that keep who wrote, at any depth, as rb's text gives a record; a receipt's
    produced.existed.by says what an output's time rests on and stays."""
    if isinstance(node, dict):
        return {k: without_writer_fields(v, k) for k, v in node.items() if k not in WRITER_FIELDS or (k == "by" and parent == "existed")}
    if isinstance(node, list):
        return [without_writer_fields(v, parent) for v in node]
    return node


def test_a_record_file_reads_with_no_writer(rec):
    """rb show <file>.yaml prints every record file, whoever wrote it, without who made each write, how rb knew,
    through what, in which session, with which agent or on which branch; --json gives the record whole."""
    files = sorted(p for p in (rec.root / ".rb").rglob("*.yaml") if not {"cache", "journal"} & set(p.parts))
    assert len(files) > 40
    for path in files:
        rel = path.relative_to(rec.root).as_posix()
        said = rec.run(Writers.HUMAN, "show", rel)
        assert said.returncode == 0 and not marks_in_text(said.stdout + said.stderr), (rel, marks_in_text(said.stdout + said.stderr))
        whole = rec.json(Writers.HUMAN, "show", rel)["data"]["record"]
        assert json.loads(said.stdout) == without_writer_fields(whole), rel
    run = rec.json(Writers.HUMAN, "show", f".rb/experiments/e1/runs/{rec.ids['run_claude']}.yaml")["data"]["record"]
    assert (run["created_by"], run["via"], run["receipt"]["session"]) == ("agent:claude-code", "cli", CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"])
    assert run["receipt"]["attached"]["branch"] == AGENT_BRANCHES[0] and run["receipt"]["agent"]["model"] == "test-model-wf-7b"


@pytest.mark.parametrize("reader", READERS)
def test_the_journal_and_reconcile_say_the_commit_and_no_branch(rec, tmp_path, reader):
    """On a branch named as an agent names its own, rb journal and rb reconcile say where the repository stands by its
    commit, never by the branch; --json keeps the branch."""
    shutil.copytree(rec.root, tmp_path / "work", symlinks=True)
    copy = Writers(tmp_path / "work")
    copy.git("add", "-A")
    copy.git("commit", "-qm", "what the tests wrote", "--allow-empty")
    copy.git("checkout", "-q", AGENT_BRANCHES[1])
    copy.rb(CLAUDE_CODE, "journal", "scan")
    head = copy.git("rev-parse", "HEAD")
    journal, reconcile = (copy.rb(READERS[reader], argv) for argv in ("journal", "reconcile"))
    assert f"git      commit {head[:10]}\n" in journal and f"The repository at {head[:7]}: 1 results folder." in reconcile
    assert not marks_in_text(journal) and not marks_in_text(reconcile), (marks_in_text(journal), marks_in_text(reconcile))
    moves = [e["branch"] for e in copy.json(READERS[reader], "journal")["data"]["entries"] if e["op"] == "git"]
    assert moves[-1] == AGENT_BRANCHES[1] and copy.json(READERS[reader], "reconcile")["data"]["repository"]["branch"] == AGENT_BRANCHES[1]


def test_no_mcp_answer_names_a_writer(rec):
    """Over MCP: the tools that read, and the briefing and status resources, carry no writer in any sentence."""
    client = rec.mcp()
    try:
        answers = [(name, client.tool(name, **args)) for name, args in [("status", {}), ("show", {"id": "q2"}), ("show", {"id": rec.ids["run_claude"]}),
                                                                         ("log", {"n": 500}), ("context", {})]]
        briefing, status = client.read("rb://context"), client.read("rb://status")
    finally:
        client.close()
    leaks = [(name, marks_in_json(result["structuredContent"])) for name, result in answers if marks_in_json(result["structuredContent"])]
    leaks += [("rb://context", marks_in_text(briefing))] if marks_in_text(briefing) else []
    leaks += [("rb://status", marks_in_json(json.loads(status)))] if marks_in_json(json.loads(status)) else []
    assert not leaks, leaks


def test_a_call_keeps_its_verb_its_date_and_its_reason(rec):
    """A call reads as made, when and why, with the user's words when given; an item asks about it the same way."""
    c1, c2 = (rec.json(Writers.HUMAN, "show", c)["data"]["finding"]["says"]["review"] for c in ("c1", "c2"))
    day = {d: rec.json(Writers.HUMAN, "show", rec.ids[d])["data"]["object"]["at"][:10] for d in ("accepted", "investigating")}
    assert (c1, c2) == (f"accepted {day['accepted']}: within budget", f"investigating since {day['investigating']} · note: one more seed")
    status = rec.rb(Writers.HUMAN, "status")
    assert f'c2 · investigating since {day["investigating"]} · note: one more seed' in status
    assert "c4 (primary: e2 gains) was retracted · note: the rule read the wrong arm · not confirmed" in status
    assert f"  {AGENT_BRANCHES[1]} at {rec.git('rev-parse', AGENT_BRANCHES[1])[:12]}: 1 note on e1 (Wide against base)" in status
    c6, c7 = (rec.json(Writers.HUMAN, "show", c)["data"]["finding"]["says"]["timing"] for c in ("c6", "c7"))
    probe = rec.json(Writers.HUMAN, "show", rec.ids["run_probe"])["data"]["object"]
    assert c6 == f"declared after the data was seen: probe was evaluated by {probe['id']} ({(probe.get('produced_at') or probe['created_at'])[:10]})"
    assert c7 == "declared; order unknown: devset, evaluated at a time nobody recorded"


def test_the_back_end_keeps_every_writer(rec):
    """The record and the fields of --json and MCP keep who made each write, how rb knew, through what, in which
    session, with which agent and on which branch, as they were."""
    lines = rec.json(Writers.HUMAN, "log", "-n", "500")["data"]["entries"]
    assert {l["actor"] for l in lines} == {HUMAN, "agent:claude-code", "agent:codex", f"agent:{MCP_CLIENT['name']}"}
    assert {"git-email", "detected:CLAUDECODE", "detected:CODEX_SANDBOX", "session:mcp"} <= {l["actor_via"] for l in lines}
    assert {l["via"] for l in lines} == {"cli", "mcp"} and [l["ignored_rb_actor"] for l in lines if "ignored_rb_actor" in l] == [HUMAN]
    assert {l.get("session") for l in lines} == {None, CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"], CODEX["CODEX_THREAD_ID"], MCP_SESSION}
    run = rec.json(Writers.HUMAN, "show", rec.ids["run_claude"])["data"]["object"]
    receipt = {k: run["receipt"][k] for k in ("actor", "actor_via", "via", "session")}
    assert receipt == {"actor": "agent:claude-code", "actor_via": "detected:CLAUDECODE", "via": "cli", "session": CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"]}
    assert run["receipt"]["attached"]["branch"] == AGENT_BRANCHES[0] and run["receipt"]["agent"]["model"] == "test-model-wf-7b"
    c2 = rec.json(Writers.HUMAN, "show", "c2")["data"]
    # the call on c2 is the user's confirmation of what Claude Code proposed, which keeps who proposed it
    assert (c2["object"]["created_by"], c2["finding"]["declared"]["by"], c2["finding"]["review"]["by"]) == ("agent:claude-code", "agent:claude-code", HUMAN)
    proposed = rec.json(Writers.HUMAN, "show", rec.ids["investigating_proposed"])["data"]
    assert (proposed["object"]["by"], proposed["authority"]["confirmed_by"]) == ("agent:claude-code", rec.ids["investigating"])
    assert {(l["question"], l["declared_by"], l["session"]) for l in c2["object"]["question_links"]} == {
        ("q1", "agent:claude-code", CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"]), ("q2", "agent:codex", CODEX["CODEX_THREAD_ID"])}
    assert sorted(d["declared_by"] for d in c2["relations"][0]["declarations"]) == ["agent:claude-code", HUMAN]
    st = rec.json(Writers.HUMAN, "status")["data"]
    assert {d["id"]: d["by"] for d in st["pending"]}[rec.ids["parked"]] == "agent:codex"
    assert [(r["id"], r["by"]) for r in st["retracted"]] == [("c4", "agent:codex")]
    assert [p["branch"] for p in st["elsewhere"]] == [AGENT_BRANCHES[1]]
    told = rec.json(CLAUDE_CODE, "note", "add", "one more", "-e", "e3")
    assert told["data"]["actor"]["id"] == "agent:claude-code"
    delta = rec.json(CODEX, "show", "e3")["rb_delta"]
    assert ("note on e3: one more", "agent:claude-code") in [(f["text"], f.get("by")) for f in delta if f["kind"] == "write"], delta


def test_a_clone_reads_with_no_writer(rec, tmp_path):
    """A clone keeps the parent's question links and relations as declared, who declared them included, and reads
    them in the same words: rb status, rb context and rb show in the clone name no writer of the parent's."""
    rec.rb(Writers.HUMAN, "export", "--with-record", "--out", str(tmp_path / "bundle"))
    rec.rb(Writers.HUMAN, "clone", str(tmp_path / "bundle"), str(tmp_path / "fork"))
    fork = Writers(tmp_path / "fork")
    links = fork.json(Writers.HUMAN, "show", "c2")["data"]["object"]["question_links"]
    assert {(l["declared_by"], l["session"]) for l in links} >= {("agent:claude-code", CLAUDE_CODE["CLAUDE_CODE_SESSION_ID"])}
    leaks = [(argv, found) for argv in (("status",), ("context",), ("show", "c2"), ("show", "q2")) for found in marks_in_text(fork.rb(Writers.HUMAN, *argv))]
    assert not leaks, leaks


def test_rb_doctor_says_how_this_shells_writes_are_recorded(rec):
    """The identity check is the one place rb's text says who you are recorded as; an RB_ACTOR an agent session
    ignores is said without the runtime's name."""
    said = rec.rb({**CLAUDE_CODE, "RB_ACTOR": HUMAN}, "doctor")
    assert f"ok   actor: agent:claude-code, from detected:CLAUDECODE (RB_ACTOR={HUMAN} is ignored in an agent session)" in said
    assert "Claude Code" not in said


def test_the_sentences_the_screens_draw_name_no_writer(rec, monkeypatch):
    """The view model's sentences (the since line, notes, calls, history, run labels, who lines, receipts) name no
    writer and no session or branch; its fields that keep who wrote are the screens' to leave out."""
    as_the_user(monkeypatch)
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
        monkeypatch.setenv(k, rec.env[k])
    view = home(rec.root, since_baseline=0)
    assert re.fullmatch(r"Since you last looked: \d+ writes, \d+ runs recorded · last \S+ · reconcile: .*", view.since), view.since
    leaks = [(path, text) for path, text in shown_strings(view) if writer_marks(text) and path != ("you",)]
    for subject in every_id(rec):
        try:
            card = object_card(subject, rec.root)
        except KeyError:            # a decision, a note or a metric has no card of its own
            continue
        leaks += [(subject, path, text) for path, text in shown_strings(card) if writer_marks(text)]
    assert not leaks, leaks
    receipt = next(r for r in view.runs if r.run_id == rec.ids["run_claude"]).receipt
    assert not {"actor", "via", "session"} & set(receipt) and "branch" not in receipt["attached"] and receipt["attached"]["commit"]
    assert {(r.by, r.session) for r in view.runs} == {("", "")}
    assert all(g["author"] == "" for b in view.briefs for g in b.overview.get("interpretations", []))
    page = rec.root / "page.html"
    rec.rb(Writers.HUMAN, "page", "--out", str(page))
    html = page.read_text(encoding="utf-8")
    # the label, then rb's reading of its criterion, then its mark where a session wrote it: never a writer's slot
    old = r'aria-describedby="g-recorded-interpretation">(Your reading|A recorded reading|The recorded reading|Recorded interpretation)</span>(<span> · its (?:rule|criterion): [^<]+</span>)?(<span> · not confirmed</span>)?</'
    # design pass 1: the label is the note's term, then its words, then rb's sentence on its rule
    new = r'aria-describedby="g-note-on-a-question"><span class="k">(Note|A note, not confirmed): </span></span>(<a class="ans"[^>]*>)?<span class="au">[^<]+</span>(</a>)?\. <span class="hs">[^<]+</span></'
    assert re.search(old, html) or re.search(new, html)
    assert "interpretation · </div>" not in html and "</a> · </p>" not in html and "Recorded interpretation</span><span> · </span>" not in html


def started(monkeypatch, capsys, *, who: str = HUMAN):
    """A question q1 and an experiment e1 filed under it, at a terminal whose writes are recorded as `who`."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", who)
    for argv in (("init", "t", "--no-agents"), ("question", "add", "Is the wide layer better?", "--id", "q1"),
                 ("experiment", "add", "x", "--id", "e1", "-q", "q1", "--baseline", "base", "--candidate", "wide")):
        assert main([*argv, "--json"]) == 0
    capsys.readouterr()


def cli(capsys, *argv) -> dict:
    code = main([*argv, "--json"])
    answer = json.loads(capsys.readouterr().out)
    assert code in (0, 1) and answer["ok"], (argv, answer)
    return answer["data"]


def test_a_calls_reason_reads_as_its_caller_wrote_it(workdir, monkeypatch, capsys):
    """A reason that holds 'human:' or 'agent:' reads as written wherever the call is said: rb's own words name no
    writer, so nothing is taken out of anyone's words."""
    started(monkeypatch, capsys)
    cli(capsys, "criterion", "add", "wide gains", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "0", "--id", "c1",
        "--when-met", "the wide layer is better")
    cli(capsys, "run", "add", "e1", "wide.top1=0.71", "base.top1=0.70")
    why = "human: checked the split by hand; agent: reran it"
    day = cli(capsys, "decide", "c1", "accept", "--why", why)["object"]["at"][:10]
    [q1] = [b for b in home().briefs if b.id == "q1"]
    assert [g["calls"] for g in q1.overview["interpretations"]] == [[f"Call on c1: accepted {day}: {why}"]]
    assert f"decided: accepted {day}: {why}" in " ".join(q1.summary)


def test_the_same_words_from_the_user_and_a_session_read_as_two_one_not_confirmed(workdir, monkeypatch, capsys):
    """Two criteria the user and a session each link to a question with the same words: the user's reads as recorded,
    the session's as not confirmed (Oct 11 evening), in the question's reading and on the screen; no writer is named,
    and the JSON keeps who declared each."""
    started(monkeypatch, capsys)
    meaning = ("-q", "q1", "--when-met", "the wide layer is no worse")
    cli(capsys, "criterion", "add", "wide gains", "-e", "e1", "--metric", "change.top1", "--at-least", "0", "--id", "c1", *meaning)
    with monkeypatch.context() as m:
        for k, v in CLAUDE_CODE.items():
            m.setenv(k, v)
        cli(capsys, "criterion", "add", "wide stays close", "-e", "e1", "--metric", "change.top1", "--at-least", "-0.5", "--id", "c2", *meaning)
    links = cli(capsys, "show", "c2")["object"]["question_links"]
    assert [l["declared_by"] for l in links] == ["agent:claude-code"]
    [q1] = [q for q in cli(capsys, "status")["questions"] if q["id"] == "q1"]
    said = [line for line in q1["inquiry"]["summary"] if line.startswith("Recorded interpretation")]
    assert [line.split(".", 1)[0] for line in said] == ["Recorded interpretation: declared when met: 'the wide layer is no worse'",
                                                        "Recorded interpretation: declared when met, not confirmed: 'the wide layer is no worse'"], said
    [brief] = [b for b in home().briefs if b.id == "q1"]
    assert [(g["text"], g["criteria"], g["confirmed"], g["author"]) for g in brief.overview["interpretations"]] == [
        ("the wide layer is no worse", ["c1"], True, ""), ("the wide layer is no worse", ["c2"], False, "")]


def test_a_weak_identity_is_said_with_no_name(workdir, monkeypatch, tmp_path_factory):
    """A screen where a yes would be recorded under this machine's login says so, and names no one."""
    as_the_user(monkeypatch)
    gitconfig = tmp_path_factory.mktemp("nobody") / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("LOGNAME", "writer-ana-login")
    assert main(["init", "t", "--no-agents", "--json"]) == 0
    assert state(workdir).log(1)[0]["actor"] == "human:writer-ana-login"
    assert home().weak_identity == "a yes is recorded under this machine's login name; set git user.email to be yourself"


def test_writes_in_a_row_that_read_the_same_are_one_line_whoever_made_them(workdir, monkeypatch, capsys):
    """The briefing's Recent activity says writes in a row that read the same once, with their count, whoever made
    each: three notes on the project in a row, by a human, Claude Code and Codex, are one line, in rb context and in
    rb://context. A line per writer would show where the writer changed."""
    started(monkeypatch, capsys)
    cli(capsys, "note", "add", "a note by the user")
    for runtime in (CLAUDE_CODE, CODEX):
        with monkeypatch.context() as m:
            for k, v in runtime.items():
                m.setenv(k, v)
            cli(capsys, "note", "add", "a note by an agent")
    writes = [line for line in state(workdir).log(10) if line["op"] != "session"]
    assert [(w["op"], w["actor"]) for w in writes[-3:]] == [("note", HUMAN), ("note", "agent:claude-code"), ("note", "agent:codex")]
    main(["context"])
    server = Mcp()
    try:
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "clientInfo": MCP_CLIENT}})
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        answer = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "rb://context"}})
    finally:
        server.close()
    for briefing in (capsys.readouterr().out, answer["result"]["contents"][0]["text"]):
        recent = briefing.split("## Recent activity\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
        notes = [line for line in recent if " note project " in line]
        assert len(notes) == 1 and notes[0].endswith(" (x3)") and recent[-1] == notes[0], recent


def test_the_writers_record_leaves_nothing_in_the_system_s_temporary_folder(tmp_path):
    """The record's helper keeps the git settings its writers run with beside the record, so a run of the suite leaves
    no folder of its own in the system's temporary folder."""
    import tempfile
    from pathlib import Path
    before = set(Path(tempfile.gettempdir()).glob("rb-writers-*"))
    w = Writers(tmp_path / "work")
    assert set(Path(tempfile.gettempdir()).glob("rb-writers-*")) == before and Path(w.env["GIT_CONFIG_GLOBAL"]).parent == tmp_path
