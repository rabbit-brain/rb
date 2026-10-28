"""The shell's model (SPRINT-OCT24.md section 1, track M): the document rb builds of a record, rb-shell-view/1.9, read
through RB_TEST_SHELL_DUMP while rb page (the Owner tier) and rb open's `/` (the Live tier) draw it under RB_TEST_SHELL.
Its fields are the contract's (tests/fixtures/shell/view-1.shape.tsv); here, those rb computes today (M0)."""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import test_shell_contract as contract
from rbtest import (AGENT_FACTS, CLAUDE_CODE, ROLE_WORDS, ROLE_WORDS_IN_AUTHOR_TEXT, USER_WORDS, WRITER_FIELDS, Open, Writers, as_the_user,
                    command, writer_marks, writers_record)
from rbtest import home as home_view
from rbtest import object_card

# the element shapes of lists every example of the contract leaves empty, which requests/m.md asks I to add at MP0: a
# record that fills one is outside the shape until the shape holds it, and only then
REQUESTED = {"questions[].changed[]", "questions[].ruled[]", "questions[].summary[]", "experiments[].notes[]", "experiments[].note_ids[]",
             "home.unplaced[]", "home.muted[]", "home.tree[].muted[]", "questions[].overview.relations[]", "experiments[].relations[]"}
# a project note's time, late on Oct 19 in UTC and on Oct 20 where it was written
LATE = "2026-10-20T01:30:00.000+08:00"


def int8_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, written by the user (human:ana, from git) at their own terminal: q1, e1 (int8 against
    fp32, three seeds) filed under it with c1, a resource with its download address, a project note with the user's
    words written late on Oct 19 in UTC, and a session's note and its proposed call on c1, which waits."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation", "--url", "https://www.image-net.org/")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
       "--when-met", "INT8 is accurate enough to ship", "--id", "c1")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        rb(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    rb({"RB_TEST_NOW": LATE}, "note", "add", "the eval host ran offline", "--words", "keep the host offline")
    rb(CLAUDE_CODE, "note", "add", "the calibration draw is fixed")
    rb(CLAUDE_CODE, "decide", "c1", "accept", "--why", "within a point on three seeds")
    return rec


@pytest.fixture(scope="module")
def int8(tmp_path_factory):
    return int8_record(tmp_path_factory.mktemp("shell-model") / "int8")


def at(clock: str) -> dict:
    """A write at `clock` on the user's own clock, east of UTC."""
    return {"RB_TEST_NOW": f"2026-10-20T{clock}:00.000+08:00"}


# a character a screen would hide, at the end of a session's reason
HIDDEN = "\u200b"


def fuller_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet with an object of each kind the document names, written by the user on a clock east
    of UTC, early on Oct 20 there and late on Oct 19 in UTC: q1 and e1 (int8 against fp32, seed and one_cycle per run)
    with its comparison; c1 with the interpretation it declares, c2, and c3 taken back; three runs, one retracted and
    made again; an analysis, a1, and one taken back, a2; a run rb saw through rb journal run that nobody recorded; and
    two calls a session proposed, on c1 and c2, the second's reason ending in a character a screen would hide, while no
    design waits."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation", "--url", "https://www.image-net.org/")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    for setting in ("seed", "one_cycle"):
        rb(you, "spec", "set", "e1", setting, "--per-run")
    rb(at("01:10"), "criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least",
       "-1", "--when-met", "INT8 is accurate enough to ship", "--id", "c1")
    rb(you, "criterion", "add", "INT8 keeps top-1 within half a point", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-0.5",
       "--id", "c2")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point, again", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
       "--id", "c3")
    rb(you, "retract", "c3", "--why", "the same rule as c1")
    runs = []
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 72.58), (75.66, 75.48)], 1):
        made = rec.json(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}", "--set", "one_cycle=1")
        runs.append(made["data"]["object"]["id"])
    rb(at("01:30"), "retract", runs[1], "--why", "evaluated on the train split")
    again = rec.json(you, "run", "add", "e1", "fp32.top1=75.71", "int8.top1=75.58", "--set", "seed=2", "--set", "one_cycle=1")["data"]["object"]["id"]
    rb(at("01:40"), "analysis", "add", "e1", "--compare", "int8/fp32:top1@pooled=-0.17[-0.29,-0.05]", "--method", "paired bootstrap over images",
       "--unit", "image", "--units", "50000", "--draws", "1000", "--reads", runs[0], "--reads", again, "--reads", runs[2], "--title",
       "per-image accuracy, int8 against fp32", "--id", "a1")
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1@pooled=-0.2[-0.3,-0.1]", "--method", "paired bootstrap over images", "--unit",
       "image", "--units", "50000", "--reads", runs[0], "--id", "a2")
    rb(you, "retract", "a2", "--why", "read the wrong split")
    # a command rb watched, which wrote seed 4's numbers and its config to files nobody recorded
    (root / "eval.py").write_text("import json, os, sys\n"
                                  "seed = int(sys.argv[sys.argv.index('--seed') + 1])\n"
                                  "os.makedirs(f'out/seed{seed}', exist_ok=True)\n"
                                  "m = {'fp32': {'top1': 75.70}, 'int8': {'top1': 75.55}}\n"
                                  "json.dump(m, open(f'out/seed{seed}/metrics.json', 'w'))\n"
                                  "open(f'out/seed{seed}/config.yaml', 'w').write(f'seed: {seed}\\none_cycle: 1\\n')\n", encoding="utf-8")
    rb(at("01:50"), "journal", "run", "--", sys.executable, "eval.py", "--seed", "4")
    rb(CLAUDE_CODE, "decide", "c1", "accept", "--why", "within a point on three seeds")
    rb(CLAUDE_CODE, "decide", "c2", "accept", "--why", "within half a point" + HIDDEN)
    rec.ids.update(runs=runs, again=again)
    return rec


def dumped(root: Path, surface: str, tmp: Path) -> tuple:
    """(document, what rb said) of `surface` (page or open) in `root`, as RB_TEST_SHELL_DUMP has rb write it."""
    dump = tmp / f"{surface}.json"
    _, said = contract.drawn(root, surface, dump=dump)
    assert dump.exists(), said
    return json.loads(dump.read_text(encoding="utf-8")), said


def both_tiers(root: Path, tmp: Path) -> dict:
    return {"live": dumped(root, "open", tmp)[0], "owner": dumped(root, "page", tmp)[0]}


@pytest.fixture(scope="module")
def docs(int8, tmp_path_factory):
    return both_tiers(int8.root, tmp_path_factory.mktemp("dumps"))


@pytest.fixture(scope="module")
def fuller(tmp_path_factory):
    return fuller_record(tmp_path_factory.mktemp("shell-model-fuller") / "int8")


@pytest.fixture(scope="module")
def fuller_docs(fuller, tmp_path_factory):
    return both_tiers(fuller.root, tmp_path_factory.mktemp("fuller-dumps"))


def at_the_record(monkeypatch, rec: Writers) -> None:
    """This test's own rb calls (the SDK's screens) as the user, in `rec`, as its writes ran."""
    as_the_user(monkeypatch)
    monkeypatch.chdir(rec.root)
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.setenv(k, rec.env[k])


@pytest.mark.parametrize("tier", ["live", "owner"])
@pytest.mark.parametrize("which", ["int8", "fuller"])
def test_the_model_writes_the_contracts_document(request, which, tier, tmp_path):
    """rb page (Owner) and rb open's `/` (Live) under RB_TEST_SHELL write the document before they draw it, while the
    renderer is not built: every field of the phases rb computes today and of the contract's own envelope is there, of
    a type the shape lists, and nothing is outside the shape but the list elements requested for it."""
    doc, _ = dumped(request.getfixturevalue(which).root, "open" if tier == "live" else "page", tmp_path)
    assert (doc["contract"], doc["tier"], doc["surface"]) == (contract.CONTRACT, tier, "open" if tier == "live" else "page")
    rows = contract.shape()
    problems, _ = contract.shape_problems(doc, rows, phases={"today", "c"})
    allowed = REQUESTED - set(rows)
    left = [p for p in problems if not ("outside the shape" in p and p.endswith(")") and p.rsplit("(", 1)[-1][:-1] in allowed)]
    assert not left, left[:20]
    if which == "int8":
        assert {q["id"] for q in doc["questions"]} == {"q1"} and [e["id"] for e in doc["experiments"]] == ["e1"]
        assert set(doc["findings"]) == {"c1"} and len(doc["runs"]) == 3 and set(doc["resources"]) == {"imagenet-val"}


@pytest.mark.parametrize("tier", ["live", "owner"])
def test_confirm_all_is_what_the_shape_holds(fuller_docs, tier):
    """Two calls a session proposed wait and no design does, where rb offers a line that confirms both: the document
    gives confirm_all as rb's line and the one command that confirms both, as listed."""
    pending = fuller_docs[tier]["pending"]
    assert [(r["kind"], r["subject"]) for r in pending["rows"]] == [("proposed", "c1"), ("proposed", "c2")] and pending["designs_line"] == ""
    types = contract.shape()["pending.confirm_all"]["types"]
    assert contract.shape_py.kind(pending["confirm_all"]) in types, (pending["confirm_all"], types)
    assert pending["confirm_all"] == {"line": "Confirm all 2", "confirm": "rb confirm " + " ".join(r["id"] for r in pending["rows"])}


def test_a_found_item_is_the_whole_command(fuller, fuller_docs, monkeypatch):
    """What rb found and nobody recorded hands the command that records it as every argv of the contract does, rb
    first and then the argument list rb ui runs, and when rb saw it to the minute in UTC."""
    at_the_record(monkeypatch, fuller)
    [seen] = home_view().found
    for tier in ("live", "owner"):
        [found] = fuller_docs[tier]["home"]["found"]
        assert found["argv"] == ["rb", *seen.argv] and found["argv"][1:6] == ["run", "add", "e1", "--from", "out/seed4/metrics.json"]
        assert (found["request_id"], found["at"]) == (seen.request_id, "2026-10-19 17:50 UTC") and seen.asked_at.startswith("2026-10-20T01:50")
        assert fuller_docs[tier]["home"]["found_total"] == 1


def held(doc: dict) -> set:
    """The ids of the objects a document holds, which a screen can link."""
    return {x["id"] for k in ("questions", "experiments") for x in doc[k]} | {i for k in ("findings", "runs", "resources", "analyses") for i in doc[k]}


def links(doc: dict) -> list:
    """Every id the document gives a screen to link, where it sits."""
    out = [(f"history.log[{r['log_index']}]", x) for r in doc["history"]["log"] for x in r["refs"]]
    for q in doc["questions"]:
        out += [(f"questions.{q['id']}.{k}", x) for k in ("findings", "experiments") for x in q[k]]
        out += [(f"questions.{q['id']}.interpretations", x) for g in q["overview"]["interpretations"] for x in g["criteria"]]
    for e in doc["experiments"]:
        out += [(f"experiments.{e['id']}.{k}", x) for k in ("findings", "run_ids") for x in e[k]]
    out += [(f"analyses.{a}.reads", x) for a, v in doc["analyses"].items() for x in v["reads"]]
    out += [(f"runs.{r}.experiment_id", v["experiment_id"]) for r, v in doc["runs"].items()]
    return out


@pytest.mark.parametrize("tier", ["live", "owner"])
def test_every_link_lands_on_an_object_the_document_holds(fuller_docs, writers, tier):
    """A criterion or an analysis taken back is not in the document, so no line of the log, no question, experiment
    or analysis links it; the log still says it was taken back, linking nothing it does not hold."""
    for doc in (fuller_docs[tier], writers[tier]):
        dead = [(where, x) for where, x in links(doc) if x not in held(doc)]
        assert not dead, dead[:10]
    log = fuller_docs[tier]["history"]["log"]
    gone = {r["sentence"].split(":")[0]: r["refs"] for r in log if r["op"] == "retract"}
    assert gone["retract criterion c3"] == [] and gone["retract analysis a2"] == []
    assert next(r["refs"] for r in log if r["op"] == "analyze" and r["sentence"].endswith("a1")) == ["a1", "e1"]
    assert "c3" not in fuller_docs[tier]["findings"] and set(fuller_docs[tier]["analyses"]) == {"a1"}


def test_a_project_note_is_in_its_parts(docs):
    """A project note is its id, its day in UTC and its text: on the user's screen with the user's words after it and
    no tag, on a page without them; a session's note says it is not confirmed; neither says who wrote it, nor rb's
    "note <id> (<day>):" line."""
    live, owner = docs["live"]["home"]["notes"], docs["owner"]["home"]["notes"]
    assert [set(n) for n in live] == [{"id", "at", "text"}] * 2 and [n["id"] for n in live] == [n["id"] for n in owner]
    [mine] = [n for n in live if n["text"].startswith("the eval host")]
    [theirs] = [n for n in live if n is not mine]
    assert mine["at"] == "2026-10-19" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", theirs["at"])
    assert mine["text"] == 'the eval host ran offline · "keep the host offline"'
    by_id = {n["id"]: n["text"] for n in owner}
    assert by_id[mine["id"]] == "the eval host ran offline"
    assert theirs["text"] == by_id[theirs["id"]] == "the calibration draw is fixed · not confirmed"
    assert not any(n["text"].startswith("note ") or n["id"] in n["text"] for n in live + owner)


def test_history_log_rows_say_what_rb_log_says(int8, docs):
    """History as log rows: every write, newest first, its minute in UTC, its op, what rb log's text says of it after
    the time, the ids it names, and its line in rb log --json; never who wrote it, and a session's line is no write."""
    entries = json.loads(subprocess.run([*command(), "log", "--json", "-n", "0"], cwd=int8.root, env=int8.env, capture_output=True,
                                        encoding="utf-8").stdout)["data"]["entries"]
    text = subprocess.run([*command(), "log", "-n", "0"], cwd=int8.root, env=int8.env, capture_output=True, encoding="utf-8").stdout.splitlines()
    writes = [i for i, e in enumerate(entries) if e["op"] != "session"]
    assert len(writes) < len(entries)                      # the session that wrote the note and the call has its own line
    for tier in ("live", "owner"):
        rows = docs[tier]["history"]["log"]
        assert [set(r) for r in rows] == [{"at", "op", "sentence", "refs", "log_index"}] * len(writes)
        assert [r["log_index"] for r in rows] == writes[::-1]
        assert [r["op"] for r in rows] == [entries[i]["op"] for i in writes[::-1]]
        # rb log's text: the time as the log keeps it, to the minute, then the sentence, where a page names what rb
        # minted an id for by what it is: a run by its label, the session's call by what it is, proposed (FIX7, FIX8)
        named = {f"run {rid}": f"run {r['label']}" for rid, r in docs[tier]["runs"].items()}
        named |= {f"decision {e['id']}": "a proposed call to accept c1, not made" for e in entries if e["kind"] == "decision"}
        said = [re.sub(r"(run|decision) \w+", lambda m: named.get(m.group(0), m.group(0)), line[17:]) for line in text[::-1]]
        assert [r["sentence"] for r in rows] == said
        minted = [*docs[tier]["runs"], *[e["id"] for e in entries if e["kind"] == "decision"]]
        assert len(named) == len(minted) == 4 and not any(i in r["sentence"] for i in minted for r in rows)
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", r["at"]) for r in rows)
    late = next(r for r in rows if r["op"] == "note" and entries[r["log_index"]]["at"] == LATE)
    assert late["at"] == "2026-10-19 17:30 UTC" and text[writes.index(late["log_index"])].startswith("2026-10-20 01:30 ")
    run = next(r for r in docs["live"]["history"]["log"] if r["op"] == "record")
    assert run["sentence"] == f"record run {docs['live']['runs'][run['refs'][0]]['label']}" and run["refs"][1:] == ["e1"]
    assert next(r for r in rows if r["op"] == "init")["refs"] == []


def test_the_owner_tier_holds_none_of_the_live_tiers_own(docs):
    """The page's document (section 1.5) has no place, no digest and no resource's place or download address, and an
    empty Reproduce address for rb export --url to set; rb open's holds where it is, its digest and the address."""
    live, owner = docs["live"], docs["owner"]
    assert "places" not in owner and "digest" not in owner and owner["reproduce_url"] == ""
    assert "reproduce_url" not in live and re.fullmatch(r"[0-9a-f]{64}", live["digest"])
    assert (owner["resources"]["imagenet-val"]["url"], live["resources"]["imagenet-val"]["url"]) == ("", "https://www.image-net.org/")
    # the download address is the url's alone: how to get it again names a recipe or a source, never the address
    assert live["resources"]["imagenet-val"]["how"] == owner["resources"]["imagenet-val"]["how"] == ""
    assert not [p for p, t in texts(owner) if "image-net.org" in t]
    assert "where_short" not in owner["resources"]["imagenet-val"] and live["resources"]["imagenet-val"]["where_short"] == []
    place, [only] = live["places"]["place"], live["places"]["places"]
    assert place["start"] is True and place["read_only"] is False and place["version"] is None
    # a single checkout is named by its branch, else its folder, on the top bar and in the Where picker: never by a
    # path, which place.path says whole for their tooltips
    assert only["id"] == place["id"] and only["can_show"] and place["line"] == only["label"] and live["places"]["elsewhere"] == []
    assert place["label"] == place["line"] and place["line"] in ("main", "master", place["path"].rstrip("/").rsplit("/", 1)[-1])
    assert "/" not in place["line"] and " \u00b7 " not in place["line"] and place["path"].startswith("/")
    assert all(f["your_words"] == "" and f["observed_places"] == [] for f in owner["findings"].values())


def test_lines_the_document_shares_with_rb_open(int8, docs, monkeypatch):
    """The model says what rb open says, from one rule each: an experiment's who line is its card's, and the line that
    says what waits for a confirmation is the head of the quiet line."""
    at_the_record(monkeypatch, int8)
    for tier in ("live", "owner"):
        doc = docs[tier]
        assert doc["experiments"][0]["who_line"] == object_card("e1").who_line == "experiment · open · not locked · 3 runs"
        assert doc["home"]["waiting_line"] == "1 call waits for your confirmation, listed apart"
        assert home_view().quiet.startswith(doc["home"]["waiting_line"] + ";")
        assert [(r["kind"], r["subject"], r["confirm"].split()[:2]) for r in doc["pending"]["rows"]] == [("proposed", "c1", ["rb", "confirm"])]


def test_the_live_digest_is_rb_opens(int8, docs, monkeypatch):
    """The Live document's digest is the one rb open's /state.json gives of the same record, live at its start place:
    its live module redraws on the same key."""
    as_the_user(monkeypatch)
    for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "RB_TELEMETRY", "DO_NOT_TRACK"):
        monkeypatch.setenv(k, int8.env[k])
    server = Open(int8.root)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(server.url + "/state.json", timeout=30) as r:
            state = json.loads(r.read().decode("utf-8"))
    finally:
        server.close()
    assert state["digest"] == docs["live"]["digest"] and state["place"]["id"] == docs["live"]["places"]["place"]["id"]


def test_what_a_screen_would_hide_is_drawn_as_its_escape(fuller_docs, docs):
    """A session's reason ending in a character a screen would hide is drawn as its escape, as rb open and rb page draw
    it, and the document says it holds one; a record with none says so too."""
    for tier in ("live", "owner"):
        doc = fuller_docs[tier]
        shown = [t for _, t in texts(doc)]
        assert doc["escaped"] is True and not [t for t in shown if HIDDEN in t]
        assert [r["text"] for r in doc["pending"]["rows"]][1] == "accept c2 · note: within half a point\\u200b"
        assert docs[tier]["escaped"] is False


def test_the_records_objects_are_its_own(fuller, fuller_docs):
    """The document holds the record's questions with the criteria filed under them, its interpretations with when
    each was declared, its analyses (one taken back is not one), and an experiment's comparison with its rows."""
    runs, again = fuller.ids["runs"], fuller.ids["again"]
    for tier in ("live", "owner"):
        doc = fuller_docs[tier]
        [q] = doc["questions"]
        assert (q["id"], q["findings"], q["experiments"]) == ("q1", ["c1", "c2"], ["e1"])
        assert q["overview"]["interpretations"] == [{"text": "INT8 is accurate enough to ship", "active": True, "confirmed": True, "criteria": ["c1"],
                                                     "holds": "Its rule is met.", "calls": [], "declared": "declared 2026-10-19 17:10 UTC"}]
        assert doc["analyses"] == {"a1": {"id": "a1", "title": "per-image accuracy, int8 against fp32", "recorded":
                                          "analysis · recorded 2026-10-19 17:40 UTC · basis reported", "reads": [runs[0], again, runs[2]], "script": "",
                                          "sha256": "", "used_by": []}}
        [e] = doc["experiments"]
        assert e["run_ids"] == [*runs, again]
        rows = [(r["variant"], r["role"], r["metrics"]["top1"]["n"], round(r["metrics"]["top1"]["mean"], 4)) for r in e["table"]["rows"]]
        assert rows == [("fp32", "baseline", 3, 75.6867), ("int8", "candidate", 3, 75.53)]
        assert e["table"]["metrics"] == [{"name": "top1", "unit": "%"}]


def test_a_run_says_its_values_as_its_card_does(fuller, fuller_docs, monkeypatch):
    """Each run's per-run values read as its card reads them, and a retracted run says the day it was taken back in
    UTC, rb's words for it and why."""
    at_the_record(monkeypatch, fuller)
    runs = fuller.ids["runs"]
    for tier in ("live", "owner"):
        doc = fuller_docs[tier]["runs"]
        assert set(doc) == {*runs, fuller.ids["again"]}
        for rid, r in doc.items():
            assert "run: " + r["per_run_text"] in object_card(rid).facts, (rid, r["per_run_text"])
        assert doc[runs[0]]["per_run_text"] == "seed=1 · one_cycle=1"
        assert doc[runs[1]]["retracted"] == {"at": "2026-10-19", "line": "retracted: evaluated on the train split", "why": "evaluated on the train split"}
        assert [rid for rid, r in doc.items() if r["retracted"]] == [runs[1]]


def texts(node, path: tuple = ()) -> list:
    """Every text of a document, wherever it sits: (path, text)."""
    if isinstance(node, dict):
        return [t for k, v in node.items() for t in texts(v, (*path, k))]
    if isinstance(node, list):
        return [t for i, v in enumerate(node) for t in texts(v, (*path, i))]
    return [(path, node)] if isinstance(node, str) else []


def keys(node, path: tuple = ()) -> list:
    """Every key of a document's objects, wherever it sits: (path, key)."""
    if isinstance(node, dict):
        return [(path, k) for k in node] + [t for k, v in node.items() for t in keys(v, (*path, k))]
    if isinstance(node, list):
        return [t for i, v in enumerate(node) for t in keys(v, (*path, i))]
    return []


def says_who(text: str) -> list:
    """What in a text says who wrote or names a role: a writer's mark, a fact rb keeps of an agent, the tag on the
    user's words that only an agent's write carries, a role word or "via", outside the record's own author text."""
    found = writer_marks(text) + [f for f in (*AGENT_FACTS, "your words") if f in text]
    for authored in ROLE_WORDS_IN_AUTHOR_TEXT:
        text = text.replace(authored, "")
    return found + [w for w in (*ROLE_WORDS, "via") if re.search(rf"\b{w}s?\b", text, re.I)]


@pytest.fixture(scope="module")
def writers(tmp_path_factory):
    rec = writers_record(tmp_path_factory.mktemp("writers") / "work")
    tmp = tmp_path_factory.mktemp("writers-dumps")
    return {"live": dumped(rec.root, "open", tmp)[0], "owner": dumped(rec.root, "page", tmp)[0]}


@pytest.mark.parametrize("tier", ["live", "owner"])
def test_the_document_names_no_writer(writers, tier):
    """Of a record a human and three agents wrote, with a write of every kind: no key that keeps who wrote, how rb knew,
    the channel, the session, the agent, a branch or a worktree, and no text that names one, on either tier. On the
    user's screen the words an agent session gave read as given, never as the user's; a page says none of the user's
    words."""
    doc = writers[tier]
    # one exemption by name: the analysis a run counts toward a criterion through (runs{}.counts_toward[].via)
    kept = [(p, k) for p, k in keys(doc) if k in WRITER_FIELDS and not (k == "via" and "counts_toward" in p)]
    assert not kept, kept[:10]
    said = [(p, t, says_who(t)) for p, t in texts(doc) if says_who(t)]
    assert not said, said[:10]
    shown = [t for _, t in texts(doc)]
    if tier == "live":
        assert any('words (as given): "make it -0.5"' in t for t in shown) and not any(t.endswith('· "make it -0.5"') for t in shown)
    else:
        assert not [w for w in USER_WORDS if any(w in t for t in shown)]


# ---------------------------------------------------------------- calls (BP 1a-2)

def calls_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet with a call of each kind rb raises for the user, written by the user at their own
    terminal unless a session is named: q1 and its sub-question q1a; e1 (int8 against fp32, three seeds and a fourth
    recorded after the calls on it) with c1 met and accepted, c2 missing with an interval that crosses its threshold,
    c3 a cited fp32 number not reproduced, c4 a close miss within its stated noise, c5 under investigation, c10 a miss a
    session accepted and the user confirmed, and c11, a primary criterion a session took back; the user's call to
    continue e1; e2 one seed missing its rule; e3 a gate missed; e4 a miss on a setting nobody found; e5 filed under no
    question, with c9 missing; c12 and c13 declared after the runs they read, on e1 and e5; c14, e2's latency, untested; a session's proposal on c2,
    waiting; a past park of e2 a session quoted from notes.md
    and the user confirmed; and a latency rb saw a command print that nobody recorded."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "README.md").write_text("# INT8 ResNet-50\n\nThe published fp32 top-1 is 76.13.\n", encoding="utf-8")
    (root / "notes.md").write_text("# Calls\n\n2026-10-01: we park the calibration study for now\n", encoding="utf-8")
    rec.git("add", "README.md", "notes.md")
    rec.git("commit", "-q", "-m", "notes")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "question", "add", "Does the calibration set matter?", "--parent", "q1", "--id", "q1a")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    for e, q in [("e1", "q1"), ("e2", "q1a"), ("e3", "q1a"), ("e4", "q1")]:
        rb(you, "experiment", "add", f"int8 against fp32, {e}", "--id", e, "-q", q, "--baseline", "fp32", "--candidate", "int8")
        rb(you, "spec", "set", e, "seed", "--per-run")
    rb(you, "experiment", "add", "int8 against fp32, not filed", "--id", "e5", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e5", "seed", "--per-run")
    rb(you, "spec", "set", "e4", "quant_backend", "--unknown")
    rule = ["--metric", "change.top1", "--at-least"]
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "-q", "q1", *rule, "-1", "--id", "c1")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a tenth of a point", "-e", "e1", "-q", "q1", *rule, "-0.1", "--id", "c2")
    rb(you, "criterion", "add", "fp32 top-1 matches the published 76.13", "-e", "e1", "-q", "q1", "--metric", "fp32.top1", "--equals", "76.13",
       "--tolerance", "0.005", "--source", "README.md", "--quote", "The published fp32 top-1 is 76.13.", "--id", "c3")
    rb(you, "criterion", "add", "INT8 keeps top-1 within 0.12", "-e", "e1", "-q", "q1", *rule, "-0.12", "--noise", "0.1", "--id", "c4")
    rb(you, "criterion", "add", "INT8 keeps top-1 within 0.13", "-e", "e1", "-q", "q1", *rule, "-0.13", "--id", "c5")
    rb(you, "criterion", "add", "INT8 keeps top-1 within 0.14", "-e", "e1", "-q", "q1", *rule, "-0.14", "--id", "c10")
    rb(you, "criterion", "add", "INT8 keeps top-1 within two points", "-e", "e1", "-q", "q1", *rule, "-2", "--id", "c11")
    rb(you, "criterion", "add", "INT8 keeps top-1 at one seed", "-e", "e2", "-q", "q1a", *rule, "0", "--id", "c6")
    rb(you, "criterion", "add", "the calibration gate", "-e", "e3", "-q", "q1a", *rule, "0", "--role", "gate", "--id", "c7")
    rb(you, "criterion", "add", "INT8 keeps top-1 with the backend unknown", "-e", "e4", "-q", "q1", *rule, "0", "--id", "c8")
    rb(you, "criterion", "add", "INT8 keeps top-1, not filed", "-e", "e5", *rule, "0", "--id", "c9")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        for e in ("e1", "e3", "e4", "e5") if seed > 1 else ("e1", "e2", "e3", "e4", "e5"):
            rb(you, "run", "add", e, f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    rb(you, "decide", "c1", "accept", "--why", "within a point on three seeds", "--words", "ship it once seed 3 lands")
    rb(you, "decide", "e1", "continue", "--why", "the int8 line goes on")
    rb(you, "decide", "c5", "investigate", "--why", "one more seed")
    rec.ids["proposed"] = rec.json(CLAUDE_CODE, "decide", "c2", "accept", "--why", "close enough")["data"]["object"]["id"]
    session_call = rec.json(CLAUDE_CODE, "decide", "c10", "accept", "--why", "per-class drop stays small")["data"]["object"]["id"]
    rb(you, "confirm", session_call)
    past = rec.json(CLAUDE_CODE, "decide", "e2", "park", "--why", "the notes say so", "--past", "--when", "2026-10-01", "--source", "notes.md:3",
                    "--quote", "we park the calibration study for now")["data"]["object"]["id"]
    rb(you, "confirm", past)
    rb(CLAUDE_CODE, "retract", "c11", "--why", "the same rule as c1, looser")
    # a fourth seed after the calls on e1: their readings move
    rb(you, "run", "add", "e1", "fp32.top1=75.70", "int8.top1=75.40", "--set", "seed=4")
    # criteria declared after the runs they read: one on e1, one on e5, which is filed under no question
    rb(you, "criterion", "add", "INT8 keeps top-1 within five points", "-e", "e1", "-q", "q1", *rule, "-5", "--id", "c12")
    rb(you, "criterion", "add", "INT8 keeps top-1 within five points, not filed", "-e", "e5", *rule, "-5", "--id", "c13")
    # a latency only e2 reads, which a command rb watched printed for seed 9, and nobody recorded
    rb(you, "metric", "add", "latency_ms", "--minimize", "--unit", "ms")
    rb(you, "criterion", "add", "INT8 serves within 10 ms", "-e", "e2", "-q", "q1a", "--metric", "int8.latency_ms", "--at-most", "10", "--id", "c14")
    (root / "bench.py").write_text("import json\nprint(json.dumps({'metrics': {'int8.latency_ms': 2.2}}))\n", encoding="utf-8")
    rb(you, "journal", "run", "--", sys.executable, "bench.py", "--seed", "9")
    return rec


@pytest.fixture(scope="module")
def calls(tmp_path_factory):
    return calls_record(tmp_path_factory.mktemp("shell-calls") / "int8")


@pytest.fixture(scope="module")
def calls_docs(calls, tmp_path_factory):
    return both_tiers(calls.root, tmp_path_factory.mktemp("calls-dumps"))


def calls_of(doc: dict) -> dict:
    return {c["subject"]: c for c in doc["calls"]}


def test_a_proposed_call_is_never_your_call(calls, calls_docs):
    """A session's accept of c2 waits apart, in pending, with its one confirm command: no call is one waiting for a
    confirmation, c2 is a call only for its own miss, and the count is the calls alone."""
    proposed = calls.ids["proposed"]
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        assert [r["id"] for r in doc["pending"]["rows"] if r["kind"] == "proposed"] == [proposed]
        assert not [c for c in doc["calls"] if c["code"] in ("call_proposed", "past_call_unconfirmed")]
        assert (calls_of(doc)["c2"]["code"], doc["your_call"]["n"]) == ("undecided", len(doc["calls"]))
        assert "c2" not in [m["subject"] for m in doc["made_calls"]]
        # an absence said of the record, never of anyone: no call on it is recorded (P05)
        assert re.match(r"c2 misses its rule and no call on it is recorded: ", calls_of(doc)["c2"]["what"]), calls_of(doc)["c2"]["what"]


def test_a_confirmed_call_keeps_the_sessions_reason_as_a_note(calls_docs):
    """A session's accept of c10, confirmed in the user's terminal, is a call made, and its reason stays the session's
    note on it: in its line and apart, as `note`."""
    for tier in ("live", "owner"):
        [made] = [m for m in calls_docs[tier]["made_calls"] if m["subject"] == "c10"]
        assert re.fullmatch(r"accepted \d{4}-\d\d-\d\d · note: per-class drop stays small", made["line"])
        assert (made["verb"], made["note"], made["question"]) == ("accepted", "per-class drop stays small", "q1")


def test_a_past_call_reads_in_its_files_voice(calls_docs):
    """A past park a session quoted from notes.md, confirmed since, reads in the file's words with its file and line,
    dated as given and Retrospective, the session's reason a note."""
    for tier in ("live", "owner"):
        [made] = [m for m in calls_docs[tier]["made_calls"] if m["subject"] == "e2"]
        assert made == {"subject": "e2", "question": "q1a", "at": "2026-10-01", "verb": "parked", "note": "the notes say so",
                        "line": 'parked 2026-10-01 (a past call from notes.md:3, "we park the calibration study for now", Retrospective) · note: the notes say so'}


def test_one_call_per_subject(calls, calls_docs):
    """Each subject a call is decided on is one call: c5, which rb status lists as made on an earlier reading and as
    under investigation, once; the missed gate c7 once, on its experiment e3, as its command decides it."""
    st = calls.json(Writers.HUMAN, "status")["data"]
    assert [i["code"] for i in st["open"] if i["subject"] == "c5" and i["who"] == "human"] == ["decision_outdated", "investigating"]
    for tier in ("live", "owner"):
        subjects = [c["subject"] for c in calls_docs[tier]["calls"]]
        assert len(subjects) == len(set(subjects))
        assert subjects == ["c1", "c2", "c3", "c4", "c5", "c10", "c6", "e3", "c8", "c9", "c11", "e1"]
        e3 = calls_of(calls_docs[tier])["e3"]
        assert (e3["kind"], e3["code"], e3["title"], e3["question"]) == ("experiment", "gate_missed", "int8 against fp32, e3", "q1a")


def test_no_pipe_in_any_line(calls_docs):
    """No line a call offers is rb status's alternation ("accept|reject"): each action is a command of its own."""
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        said = [doc["home"]["quiet"]] + [x for c in doc["calls"] for a in c["actions"] for x in (a["line"], a["consequence"], *a["argv"])]
        said += [x for c in doc["calls"] for x in c["reason"].values()]
        assert said and not [x for x in said if "|" in x]


def test_the_actions_of_each_kind_of_call(calls_docs):
    """A result to review offers accept, reject and investigate; a missed gate park (with when to look again), continue
    and stop on its experiment; a call made on an earlier reading on an experiment the call it revisits first, then the
    rest of its kind; each as argv, as one line to copy, and as what it reads as once made, with no writer."""
    why, day = "‹why›", "‹day›"
    for tier in ("live", "owner"):
        c = calls_of(calls_docs[tier])
        assert c["c2"]["actions"] == [
            {"action": a, "label": a.capitalize(), "argv": ["rb", "decide", "c2", a, "--why", why], "line": f"rb decide c2 {a} --why '{why}'",
             "consequence": f"{made} {day}: {why}"} for a, made in [("accept", "accepted"), ("reject", "rejected"), ("investigate", "investigating since")]]
        assert c["e3"]["actions"][0] == {"action": "park", "label": "Park", "argv": ["rb", "decide", "e3", "park", "--why", why, "--revisit", "‹when to look again›"],
                                         "line": f"rb decide e3 park --why '{why}' --revisit '‹when to look again›'", "consequence": f"parked {day}: {why}"}
        assert [a["action"] for a in c["e3"]["actions"]] == ["park", "continue", "stop"]
        assert [a["action"] for a in c["e1"]["actions"]] == ["continue", "park", "stop", "pivot", "ship"]
        assert [a["action"] for a in c["c1"]["actions"]] == ["accept", "reject", "investigate"]
        assert [a["action"] for a in c["c11"]["actions"]] == ["accept"]


def test_the_commands_rb_status_gives_are_the_actions_a_call_offers(calls):
    """rb status's command for each call names the very actions the screens offer, one rule for both."""
    st = calls.json(Writers.HUMAN, "status")["data"]
    for i in st["open"]:
        m = re.match(r"rb decide (\S+) ([a-z|]+) --why", i["do"])
        if i["who"] == "human" and m and "|" in m.group(2):
            assert m.group(2) in ("accept|reject|investigate", "park|continue|stop", "accept|reject"), i["do"]


@pytest.mark.parametrize("subject, reason", [
    ("c1", {"label": "Made on an earlier reading", "detail": "was -0.1567 [-0.2192, -0.09415], n 3 (rb): loss, criterion met", "also": ""}),
    ("e1", {"label": "Made on an earlier reading", "detail": "moved since: c1, c2, c3, c4 and 4 more", "also": ""}),
    ("c11", {"label": "Retracted", "detail": "primary criterion", "also": ""}),
    ("e3", {"label": "Gate missed", "detail": "INT8 is 0.16 points lower than the FP32 baseline (-0.22 to -0.09)", "also": ""}),
    ("c8", {"label": "Integrity problem", "detail": "unknown settings", "also": ""}),
    ("c6", {"label": "Too little evidence", "detail": "1 run · rule not met", "also": ""}),
    ("c4", {"label": "Rule not met", "detail": "within the stated noise (0.1) of the rule", "also": ""}),
    ("c3", {"label": "Not reproduced", "detail": "estimate 75.69 outside 76.13 ± 0.005", "also": ""}),
    ("c2", {"label": "Rule not met", "detail": "estimate -0.1925 < -0.1", "also": "on the estimate, not across the whole interval"}),
])
def test_each_reason_is_rbs_rule_words(calls_docs, subject, reason):
    """Each call leads with why rb asks (SHELL-SPEC 15.5, first match wins): a call made on an earlier reading, a
    criterion a session took back, a missed gate, an integrity problem, too little evidence, a close miss, a cited
    number not reproduced, and a miss on the estimate alone, each in rb's words and the finding's."""
    for tier in ("live", "owner"):
        assert calls_of(calls_docs[tier])[subject]["reason"] == reason


def test_an_investigation_says_since_when(tmp_path):
    """An investigation under way, on a reading that has not moved, reads Investigating, since the call's day."""
    rec = Writers(tmp_path / "int8")
    (tmp_path / "int8").mkdir()
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "criterion", "add", "INT8 keeps top-1", "-e", "e1", "--metric", "change.top1", "--at-least", "0", "--id", "c1")
    rb(you, "run", "add", "e1", "fp32.top1=75.69", "int8.top1=75.53")
    rb({"RB_TEST_NOW": "2026-10-14T09:00:00.000+00:00"}, "decide", "c1", "investigate", "--why", "one more seed")
    (tmp_path / "dumps").mkdir()
    doc = both_tiers(rec.root, tmp_path / "dumps")["live"]
    [call] = doc["calls"]
    assert (call["code"], call["reason"], [a["action"] for a in call["actions"]]) == ("investigating", {"label": "Investigating", "detail": "since 2026-10-14", "also": ""},
                                                                                       ["accept", "reject"])


def test_every_waiting_call_is_listed(calls_docs):
    """Every call waiting on you is listed under a question the document holds, or with the work filed under none: c9
    in the unfiled group, c11 (taken back, so no inquiry lists it) where its experiment is filed."""
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        listed = {q["id"]: q["calls"] for q in doc["questions"]}
        listed[":unfiled"] = doc["unfiled"]["calls"]
        for c in doc["calls"]:
            assert c["subject"] in listed[c["question"]], c
        assert sorted(x for xs in listed.values() for x in xs) == sorted(c["subject"] for c in doc["calls"])
        assert (calls_of(doc)["c9"]["question"], calls_of(doc)["c11"]["question"]) == (":unfiled", "q1")


def test_your_call_counts_one_source(calls, calls_docs, monkeypatch):
    """Your call's N is the calls alone, one per subject, in the document, its quiet line, /state.json and rb ui's
    home, and it is the subjects rb status asks the user to decide, the calls waiting for a confirmation left out."""
    st = calls.json(Writers.HUMAN, "status")["data"]
    exp_of = {f["criterion"]: f["experiment"] for f in st["findings"]}
    subjects = {exp_of[i["subject"]] if i["code"] == "gate_missed" else i["subject"] for i in st["open"]
                if i["who"] == "human" and i["code"] not in ("call_proposed", "past_call_unconfirmed")}
    at_the_record(monkeypatch, calls)
    h = home_view()
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        n = doc["your_call"]["n"]
        assert n == len(doc["calls"]) == len(subjects) == h.waiting == len(h.calls) == 12
        assert doc["home"]["quiet"] == h.quiet == "12 calls wait on you."
        assert doc["your_call"]["first"] == doc["calls"][0]["subject"]


def test_found_is_never_a_call(calls_docs):
    """What rb found that nobody recorded is listed as found, never as a call or in the count, and the quiet line says
    no "more" beside it."""
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        assert doc["home"]["found_total"] == 1 and doc["home"]["found"][0]["argv"][:4] == ["rb", "run", "add", "e2"]
        assert doc["your_call"]["n"] == len(doc["calls"]) and "more" not in doc["home"]["quiet"]


def test_open_items_grouped(calls_docs):
    """What is still open in the record, not the user's to call, is grouped by rb's code in rb's order, each group
    headed in rb's words and counted; a late item gives each criterion's units produced before it was declared; the
    open items on work filed under no question sit with that work."""
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        groups = doc["open_items"]
        assert [(g["code"], g["head"], g["n"], len(g["items"])) for g in groups] == [
            ("untested", "Rules with no runs yet", 1, 1), ("late", "Rules written after their results or data were seen", 1, 1),
            ("unknown", "Required settings with no value yet", 1, 1)]
        # DESIGN-PASS-1 target 2: one item per rule, by its statement, its units in its sentence
        late = groups[1]["items"][0]
        assert {k: late[k] for k in ("subject", "units", "criteria")} == {"subject": "c12", "units": "", "criteria": ["c12"]}
        assert late["what"] == doc["findings"]["c12"]["statement"] + ": written after 4 of its 4 results came in", late
        assert doc["unfiled"]["line"] == "Work linked to no question" and doc["unfiled"]["criteria"] == ["c9", "c13"]
        assert [(i["subject"], i["code"], i["what"]) for i in doc["unfiled"]["open_items"]] == [
            ("c13", "late", doc["findings"]["c13"]["statement"] + ": written after 3 of its 3 results came in")]



def gates_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, with what Your call must not lose: three gates of e1 that miss, one call on e1;
    c3, a criterion of e1 linked to no question while the record links the others; c4, accepted before any run read
    it; the user's vouch for e1's calibration setting; e1 accepted before its runs; and e2's c5, missing on one
    configuration run twice."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "spec", "set", "e1", "calibration", "imagenet-train-1k", "--source", "note:the calibration images")
    rule = ["--metric", "change.top1", "--at-least"]
    rb(you, "criterion", "add", "INT8 gains top-1", "-e", "e1", "-q", "q1", *rule, "0", "--role", "gate", "--id", "g1")
    rb(you, "criterion", "add", "INT8 gains a tenth of top-1", "-e", "e1", "-q", "q1", *rule, "0.1", "--role", "gate", "--id", "g2")
    rb(you, "criterion", "add", "INT8 reaches 80 top-1", "-e", "e1", "-q", "q1", "--metric", "int8.top1", "--at-least", "80", "--role", "gate", "--id", "g3")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a tenth", "-e", "e1", *rule, "-0.1", "--id", "c3")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "-q", "q1", *rule, "-1", "--id", "c4")
    rb(you, "decide", "c4", "accept", "--why", "the budget is a point")
    rb(you, "decide", "e1/calibration", "accept", "--why", "read off the calibration script")
    rb(you, "decide", "e1", "accept", "--why", "the int8 line is set")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        rb(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    # e2: one configuration, run twice, missing its rule
    rb(you, "experiment", "add", "int8 against fp32, one configuration", "--id", "e2", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e2", "config_id", "--per-run")
    rb(you, "spec", "set", "e2", "population.unit", "config_id", "--no-verify")
    rb(you, "criterion", "add", "INT8 gains top-1 on one configuration", "-e", "e2", "-q", "q1", *rule, "0", "--id", "c5")
    rb(you, "run", "add", "e2", "fp32.top1=75.70", "int8.top1=75.50", "--set", "config_id=a")
    rb(you, "run", "add", "e2", "fp32.top1=75.70", "int8.top1=75.40", "--set", "config_id=a", "--again", "--why", "a second execution")
    return rec


@pytest.fixture(scope="module")
def gates(tmp_path_factory):
    return gates_record(tmp_path_factory.mktemp("shell-gates") / "int8")


@pytest.fixture(scope="module")
def gates_docs(gates, tmp_path_factory):
    return both_tiers(gates.root, tmp_path_factory.mktemp("gates-dumps"))


def test_two_missed_gates_are_one_call_that_names_each(gates, gates_docs):
    """Three missed gates of e1 are one call on e1, whose reason gives each gate's comparison, and which settles e1's
    accept made on an earlier reading too; rb status's one item for the gates says each gate's own words, so no
    gate reads another's comparison."""
    st = gates.json(Writers.HUMAN, "status")["data"]
    [item] = [i for i in st["open"] if i["code"] == "gate_missed"]
    assert item["subject"] == "g1, g2, g3"
    # g1 and g2 read the same comparison and g3 another: each gate's words in turn, its own comparison in them
    said = item["what"].split("; ")
    assert [w.split(" ")[0] for w in said] == ["g1", "g2", "g3"]
    assert said[0].startswith("g1 gate missed (change.top1: ") and said[2].startswith("g3 gate missed (int8.top1: 75."), said
    for tier in ("live", "owner"):
        [call] = [c for c in gates_docs[tier]["calls"] if c["code"] == "gate_missed"]
        assert (call["subject"], call["reason"]["label"]) == ("e1", "Gate missed")
        # DESIGN-PASS-1 P8: each gate by its statement, then its reading in plain words, never rb's comparison text
        assert re.fullmatch(r"INT8 gains top-1: INT8 is [^;]+; INT8 gains a tenth of top-1: INT8 is [^;]+; INT8 reaches 80 top-1: INT8 is 75\.\d\d% \(75\.\d\d% to 75\.\d\d%\)",
                            call["reason"]["detail"]), call["reason"]
        assert call["what"] == item["what"]
        # e1's accept, made before its runs, is on an earlier reading: the one call offers its calls after the gate's
        assert call["reason"]["also"].startswith("Made on an earlier reading"), call["reason"]
        assert [a["action"] for a in call["actions"]] == ["park", "continue", "stop", "accept", "reject", "investigate"]



def test_no_absence_is_said_of_anyone(gates_docs):
    """No text of either document says an absence of anyone (the shell's lint P05), only of the record: c5's miss with
    no call on it is "no call on it is recorded"."""
    p05 = re.compile(r"\bno (?!recorded\b)(?:runs?|criteri(?:on|a)|experiments?|analys[ie]s|settings?|results?) (?:is read|reads?|cites?)\b"
                     r"|\bnothing (?!in the record\b)(?:reads|cites)\b|\b(?:nobody|no[ -]one)\b(?!(?: else)? recorded\b)")
    for tier in ("live", "owner"):
        said = [t for _, t in texts(gates_docs[tier])]
        assert any(t.startswith("c5 misses its rule and no call on it is recorded: ") for t in said), tier
        assert not [t for t in said if p05.search(t)], [t for t in said if p05.search(t)][:5]


def test_a_setting_no_source_states_is_said_unchecked_in_the_record(tmp_path, monkeypatch):
    """lr given with no source is provisional, and its open item says so of the record ("not checked"),
    in both documents, never as an absence of anyone (P05)."""
    as_the_user(monkeypatch)
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    for argv in (("init", "INT8 ResNet-50 on ImageNet", "--no-agents"), ("experiment", "add", "INT8 against FP32", "--id", "e1"), ("spec", "set", "e1", "lr", "0.1")):
        rec.rb(Writers.HUMAN, *argv)
    for tier, doc in both_tiers(root, tmp_path).items():
        said = [t for _, t in texts(doc)]
        assert "lr in e1: given as 0.1, not checked against a source." in said, (tier, [t for t in said if "0.1" in t])
        assert not [t for t in said if re.search(r"\b(?:nobody|no[ -]one)\b(?!(?: else)? recorded\b)", t)], tier


def test_too_little_evidence_names_the_unit_in_plain_words(gates_docs):
    """c5 rests on one configuration run twice: Too little evidence names it as rb's string table does."""
    for tier in ("live", "owner"):
        assert calls_of(gates_docs[tier])["c5"]["reason"] == {"label": "Too little evidence", "detail": "1 configuration · rule not met", "also": ""}

def test_a_criterion_linked_to_no_question_is_called_with_that_work(gates_docs):
    """c3, of an experiment filed under q1 but linked to no question where the record links its others, is called with
    the work linked to no question, which the screens call so."""
    for tier in ("live", "owner"):
        doc = gates_docs[tier]
        assert calls_of(doc)["c3"]["question"] == ":unfiled" and "c3" in doc["unfiled"]["calls"]
        assert (doc["unfiled"]["line"], doc["unfiled"]["criteria"]) == ("Work linked to no question", ["c3"])
        assert "c3" not in [c for q in doc["questions"] for c in q["calls"]]


def test_a_call_made_before_any_reading_says_so(gates_docs):
    """c4, accepted before any run, is made on an earlier reading: made when there was no reading."""
    for tier in ("live", "owner"):
        assert calls_of(gates_docs[tier])["c4"]["reason"] == {"label": "Made on an earlier reading", "detail": "made when there was no reading", "also": ""}


def test_a_call_on_a_setting_is_where_its_experiment_is(gates_docs):
    """The user's vouch for e1/calibration is a call made, where e1 is filed."""
    for tier in ("live", "owner"):
        [made] = [m for m in gates_docs[tier]["made_calls"] if m["subject"] == "e1/calibration"]
        assert (made["verb"], made["question"]) == ("accepted", "q1")


def test_one_call_on_a_subject_says_each_reason_and_offers_each_action(calls, calls_docs, monkeypatch):
    """c5, which rb status lists as made on an earlier reading and as under investigation, is one call that leads
    with the first and says the investigation after it, offering every action either offers; q1's overview lists it
    once."""
    for tier in ("live", "owner"):
        doc = calls_docs[tier]
        c5 = calls_of(doc)["c5"]
        assert c5["code"] == "decision_outdated" and c5["reason"]["label"] == "Made on an earlier reading"
        assert re.fullmatch(r"Investigating · since \d{4}-\d\d-\d\d", c5["reason"]["also"]), c5["reason"]
        assert [a["action"] for a in c5["actions"]] == ["accept", "reject", "investigate"]
    at_the_record(monkeypatch, calls)
    [q1] = [b for b in home_view().briefs if b.id == "q1"]
    listed = [c["subject"] for c in q1.overview["calls"]]
    assert listed.count("c5") == 1 and len(listed) == len(set(listed))


def test_every_open_item_head_keeps_rbs_word_bans():
    """Every heading rb gives a group of open items (ledger/views.rs open_head, read from its source, since a record
    shows only the codes it holds) keeps the word bans the page's lint holds rb's words to, with its exemptions."""
    import test_shell as lint
    src = (Path(__file__).parent.parent / "rust" / "src" / "ledger" / "views.rs").read_text(encoding="utf-8")
    body = src[src.index("pub fn open_head("):]
    body = body[:body.index("\n}\n")]
    heads = re.findall(r'=> (?:return \()?"([^"]+)"', body)
    assert len(heads) > 40 and "Metrics the runs report that no rule reads" in heads
    hits = []
    for h in heads:
        said = h
        for _, kept, _ in lint.EXEMPT:
            said = re.sub(kept, " ", said, flags=re.I)
        hits += [(ident, h) for ident, _, rx in lint.BANNED if re.search(rx, said, flags=re.I)]
    assert not hits, hits


def findings_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, for a finding's own fields (BP 1b-1): e1 with c1 (three seeds, an interval), c2 (a
    cited fp32 number README.md states, not reproduced) and c3 (an analysis a1 that reads e1's runs); e2, "calibration
    set size: ...", its arms recorded apart by photo, an fp32 run with no int8 beside it; e3 locked, its unit amended
    to photo after the lock and c7 added by an amendment; q2's interpretations: c4 a point only declared first, c5 with
    an interval; q3's a session's, not confirmed, on c6 (an interval) and c8 (e4's one seed)."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "README.md").write_text("# INT8 ResNet-50\n\nThe published fp32 top-1 is 76.13.\n", encoding="utf-8")
    rec.git("add", "README.md")
    rec.git("commit", "-q", "-m", "readme")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    for q, text in [("q1", "Does INT8 cost ResNet-50 top-1 on ImageNet?"), ("q2", "Does INT8 keep top-1 per class?"), ("q3", "Does INT8 keep its latency gain?")]:
        rb(you, "question", "add", text, "--id", q)
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    for e, title in [("e1", "int8 against fp32"), ("e2", "calibration set size: how many images INT8 needs"), ("e3", "e3: per-photo reading"),
                     ("e4", "one seed"), ("e5", "three seeds")]:
        rb(you, "experiment", "add", title, "--id", e, "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    for e in ("e1", "e4", "e5"):
        rb(you, "spec", "set", e, "seed", "--per-run")
    rule = ["--metric", "change.top1", "--at-least"]
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "-q", "q1", *rule, "-1", "--id", "c1")
    rb(you, "criterion", "add", "fp32 top-1 matches the published 76.13", "-e", "e1", "-q", "q1", "--metric", "fp32.top1", "--equals", "76.13",
       "--tolerance", "0.005", "--source", "README.md", "--quote", "The published fp32 top-1 is 76.13.", "--id", "c2")
    rb(you, "criterion", "add", "INT8 keeps pooled top-1 within half a point", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--estimand", "pooled",
       "--at-least", "-0.5", "--id", "c3")
    runs = []
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        runs.append(rec.json(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")["data"]["object"]["id"])
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1@pooled=-0.17[-0.29,-0.05]", "--method", "paired bootstrap over images",
       "--unit", "image", "--units", "50000", "--reads", ",".join(runs), "--id", "a1")
    rb(you, "criterion", "add", "INT8 is no worse than 0.3 of top-1", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--noninferior", "0.3", "--id", "c15")
    rb(you, "criterion", "add", "INT8 keeps per-class top-1 within a point", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--estimand", "per_class",
       "--at-least", "-1", "--id", "c16")
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1@per_class=-0.2", "--method", "per-class mean", "--id", "a3")
    # e2: the arms recorded apart, three photos each, and one fp32 photo with no int8 beside it
    rb(you, "spec", "set", "e2", "photo", "--per-run")
    rb(you, "spec", "set", "e2", "population.unit", "photo", "--no-verify")
    rb(you, "criterion", "add", "INT8 keeps per-photo top-1 within a point", "-e", "e2", "-q", "q1", *rule, "-1", "--id", "c9")
    for photo, (fp32, int8) in enumerate([(75.7, 75.5), (75.6, 75.5), (75.8, 75.6)], 1):
        rb(you, "run", "add", "e2", f"top1={fp32}", "--variant", "fp32", "--set", f"photo=p{photo}")
        rb(you, "run", "add", "e2", f"top1={int8}", "--variant", "int8", "--set", f"photo=p{photo}")
    rb(you, "run", "add", "e2", "top1=75.9", "--variant", "fp32", "--set", "photo=p4")
    # e3: locked, then its unit amended and a criterion added, then its runs
    rb(you, "spec", "set", "e3", "photo", "--per-run")
    rb(you, "spec", "set", "e3", "calibration", "train (1k) -> val", "--no-verify")
    rb(you, "criterion", "add", "INT8 keeps top-1 on e3", "-e", "e3", "-q", "q1", *rule, "-1", "--id", "c10")
    rb(you, "freeze", "e3", "-m", "before the photos")
    rb(you, "spec", "set", "e3", "population.unit", "photo", "--no-verify", "--amend", "--why", "the photos are the unit")
    rb(you, "criterion", "add", "INT8 keeps top-1 within two points on e3", "-e", "e3", "-q", "q1", *rule, "-2", "--id", "c11", "--amend", "--why", "a looser bound")
    # a session amends e3 too: its reason reads as its note
    rb(CLAUDE_CODE, "spec", "set", "e3", "calibration", "train (10k) -> val", "--no-verify", "--amend", "--why", "the calibration images")
    for photo in (1, 2):
        rb(you, "run", "add", "e3", "fp32.top1=75.7", "int8.top1=75.5", "--set", f"photo=p{photo}")
    # q2: c4 a point only over 500 images, declared first; c5 an interval over three seeds
    rb(you, "criterion", "add", "INT8 keeps pooled per-class top-1", "-e", "e4", "-q", "q2", "--metric", "change.top1", "--estimand", "pooled",
       "--at-least", "-1", "--when-met", "INT8 keeps every class", "--id", "c4")
    rb(you, "criterion", "add", "INT8 keeps per-class top-1 at three seeds", "-e", "e5", "-q", "q2", *rule, "-1", "--when-met", "INT8 keeps most classes", "--id", "c5")
    e4 = rec.json(you, "run", "add", "e4", "fp32.top1=75.7", "int8.top1=75.6", "--set", "seed=1")["data"]["object"]["id"]
    rb(you, "analysis", "add", "e4", "--compare", "int8/fp32:top1@pooled=-0.1", "--method", "pooled over images", "--unit", "image", "--units", "500",
       "--reads", e4, "--id", "a2")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        rb(you, "run", "add", "e5", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    # q4: two interpretations read with an interval, c13 on three seeds declared first, c14 on four
    rb(you, "experiment", "add", "four seeds", "--id", "e7", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e7", "seed", "--per-run")
    rb(you, "question", "add", "Does INT8 keep top-1 on the hard classes?", "--id", "q4")
    rb(you, "criterion", "add", "INT8 keeps hard-class top-1 at three seeds", "-e", "e5", "-q", "q4", *rule, "-1", "--when-met", "INT8 keeps the hard classes", "--id", "c13")
    rb(you, "criterion", "add", "INT8 keeps hard-class top-1 at four seeds", "-e", "e7", "-q", "q4", *rule, "-1", "--when-met", "INT8 keeps the hardest classes", "--id", "c14")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48), (75.70, 75.52)], 1):
        rb(you, "run", "add", "e7", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    # q3: a session's interpretation on two criteria, one met and one met on its estimate alone
    rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 on e5", "-e", "e5", "-q", "q3", *rule, "-1", "--when-met", "INT8 keeps its latency gain", "--id", "c6")
    rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 on e4", "-e", "e4", "-q", "q3", *rule, "-1", "--when-met", "INT8 keeps its latency gain", "--id", "c8")
    # e8: locked with lr 1 and wd 0, then amended back and forth, a variant's setting, one made unknown and taken
    # back, and its unit amended, taken back and set again
    rb(you, "experiment", "add", "int8 against fp32, amended", "--id", "e8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    for k, v in [("lr", "1"), ("wd", "0"), ("int8.precision", "int8"), ("population.unit", "photo")]:
        rb(you, "spec", "set", "e8", k, v, "--no-verify")
    rb(you, "criterion", "add", "INT8 keeps top-1 on e8", "-e", "e8", "-q", "q1", *rule, "-1", "--id", "c20")
    rb(you, "freeze", "e8", "-m", "before its runs")
    for k, v in [("lr", "2"), ("lr", "1"), ("wd", "5"), ("wd", "0"), ("int8.precision", "int4"), ("population.unit", "image")]:
        rb(you, "spec", "set", "e8", k, v, "--no-verify", "--amend", "--why", f"{k} to {v}")
    rb(you, "spec", "set", "e8", "calib", "--unknown", "--amend", "--why", "the calibration set")
    rb(you, "retract", "e8/calib", "--why", "not a setting of this run")
    rb(you, "retract", "e8/population.unit", "--why", "declared again")
    rb(you, "spec", "set", "e8", "population.unit", "image", "--no-verify", "--amend", "--why", "image again")
    rb(you, "spec", "set", "e8", "wd", "0", "--optional", "--no-verify", "--amend", "--why", "wd may go unrecorded")
    rb(you, "spec", "set", "e8", "image", "--per-run", "--amend", "--why", "each run's image")
    rb(you, "run", "add", "e8", "fp32.top1=75.7", "int8.top1=75.5", "--set", "image=i1")
    # e6: INT8 ahead on two seeds of three, the reverse of e1's c1, declared so
    rb(you, "experiment", "add", "int8 against fp32, the fp16 export", "--id", "e6", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e6", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps top-1 on the fp16 export", "-e", "e6", "-q", "q1", *rule, "-1", "--id", "c12")
    for seed, (fp32, int8) in enumerate([(75.6, 75.9), (75.7, 75.6), (75.6, 76.1)], 1):
        rb(you, "run", "add", "e6", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    rb(you, "criterion", "relate", "c12", "reverses", "c1", "--orientation", "same", "--why", "the same comparison, another export")
    rec.ids["runs"] = runs
    return rec


@pytest.fixture(scope="module")
def m3(tmp_path_factory):
    return findings_record(tmp_path_factory.mktemp("shell-findings") / "int8")


@pytest.fixture(scope="module")
def m3_docs(m3, tmp_path_factory):
    return both_tiers(m3.root, tmp_path_factory.mktemp("findings-dumps"))


def test_reading_parts_are_rbs_text(m3_docs, calls_docs, docs):
    """A finding's reading in parts is rb's comparison text in parts: its estimate, interval and unit are what
    says.comparison says after what it reads, and what the row's value says; its numbers are the comparison's."""
    seen = 0
    for doc in (m3_docs["owner"], m3_docs["live"], calls_docs["owner"], docs["owner"]):
        for f in doc["findings"].values():
            r = f["reading"]
            if r["estimate"] is None:
                assert (r["estimate_text"], r["interval_text"], r["unit_text"]) == ("", "", "") and f["row"]["value"] == "no reading yet"
                continue
            head = " ".join(x for x in (r["estimate_text"], r["interval_text"], r["unit_text"]) if x)
            said = f["says"]["comparison"]
            assert said.split(": ", 1)[1].startswith(head + " · ") or said.split(": ", 1)[1].startswith(head + " ("), (head, said)
            assert f["row"]["value"] == head
            assert (r["lo"] is None) == (r["interval_text"] == "") and (r["level"] is None) == (r["lo"] is None)
            seen += 1
    assert seen > 10
    r = m3_docs["owner"]["findings"]["c1"]["reading"]
    assert {k: r[k] for k in ("estimate_text", "interval_text", "unit_text", "method", "n", "level", "result")} == {
        "estimate_text": "-0.1567", "interval_text": "[-0.2192, -0.09415]", "unit_text": "%", "method": "paired difference, t interval", "n": 3,
        "level": 0.95, "result": "loss"}


@pytest.mark.parametrize("cid, line", [
    ("c1", "3 runs"),
    ("c3", "analysis a1 · reads 3 runs"),
    ("c9", "3 photos from 6 runs · 3 runs per arm · 1 run not counted"),
    ("c10", "2 runs"),
    ("c4", "analysis a2 · reads 1 run"),
    ("c16", "analysis a3"),
])
def test_the_units_line_in_plain_words(m3_docs, cid, line):
    """The units line: runs where each unit is one run; else the unit's plain noun and the runs it holds, how the arms
    split where they are recorded apart, and the runs not counted; an analysis's reading rests on it and the runs it
    reads."""
    for tier in ("live", "owner"):
        assert m3_docs[tier]["findings"][cid]["units_line"] == line


def test_rule_detail_on_a_miss_only(m3_docs):
    """rule_detail is rb's rule sentence after its rule word, on a miss alone: c2 not reproduced says what its band is;
    a met rule says nothing more."""
    for tier in ("live", "owner"):
        fs = m3_docs[tier]["findings"]
        assert fs["c2"]["rule_detail"] == "estimate 75.69 outside 76.13 ± 0.005" and fs["c2"]["says"]["rule"] == "not reproduced: " + fs["c2"]["rule_detail"]
        assert [c for c, f in fs.items() if f["rule_detail"]] == [c for c, f in fs.items() if f["standing"].split(" · ")[0] in (
            "criterion not met", "not reproduced", "gate missed", "criterion undetermined", "gate undetermined")]
        assert fs["c1"]["rule_detail"] == ""


def test_unit_amended_after_the_lock(m3_docs):
    """e3's unit, amended to photo after the lock, says so on each finding declared before it that reads its runs,
    linking the amendment, which e3's amendments hold with its why; c11, added by an amendment after it, is locked with
    its own and reads no unit amended; a setting's values are handed apart, whatever words they hold."""
    for tier in ("live", "owner"):
        doc = m3_docs[tier]
        u = doc["findings"]["c10"]["unit_amended"]
        assert {k: u[k] for k in ("setting", "was", "now", "amendment", "why")} == {"setting": "population.unit", "was": "run", "now": "photo",
                                                                                   "amendment": "e3:am:1", "why": "the photos are the unit"}
        assert re.fullmatch(r"unit amended after the lock: run -> photo, \d{4}-\d\d-\d\d \d\d:\d\d UTC", u["line"]) and u["line"].endswith(u["at"])
        assert doc["findings"]["c1"]["unit_amended"] is None
        [e3] = [x for x in doc["experiments"] if x["id"] == "e3"]
        am = {a["id"]: a for a in e3["amendments"]}
        assert list(am) == ["e3:am:1", "e3:am:2", "e3:am:3"]
        assert (am["e3:am:3"]["why"], am["e3:am:3"]["changes"]) == ("note: the calibration images · not confirmed",
                                                                  [{"setting": "calibration", "was": "train (1k) -> val", "now": "train (10k) -> val"}])
        assert (am["e3:am:1"]["line"], am["e3:am:1"]["changes"], am["e3:am:1"]["why"], am["e3:am:1"]["refs"]) == (
            "unit amended after the lock: run -> photo", [{"setting": "population.unit", "was": "", "now": "photo"}], "the photos are the unit", ["c10"])
        assert am["e3:am:2"]["changes"] == [{"setting": "criteria", "was": "", "now": "c11"}] and am["e3:am:2"]["refs"] == ["c11"]
        rows = {r[0]: r for r in doc["findings"]["c11"]["receipt_rows"]}
        assert rows["locked"][1] == am["e3:am:2"]["at"] + ", with amendment e3:am:2" and rows["locked"][2] == ["e3:am:2"]
        # c11, declared under photo, reads no unit amended
        assert doc["findings"]["c11"]["unit_amended"] is None and "amended" not in rows
        rows = {r[0]: r for r in doc["findings"]["c10"]["receipt_rows"]}
        assert rows["locked"][1].endswith(", with e3") and rows["amended"] == ["amended", u["at"] + ": unit run -> photo", ["e3:am:1"]]



def test_each_amendment_says_its_own_change(m3_docs):
    """e8's amendments, set back and forth to values its lock held: each says the setting it changed and its values
    then; a retraction changes no value; its unit, amended, taken back and declared again, reads as amended from what it
    was when c20 was declared to what it is."""
    for tier in ("live", "owner"):
        [e8] = [x for x in m3_docs[tier]["experiments"] if x["id"] == "e8"]
        said = [[(c["setting"], c["was"], c["now"]) for c in a["changes"]] for a in e8["amendments"]]
        assert said == [[("lr", "1", "2")], [("lr", "2", "1")], [("wd", "0", "5")], [("wd", "5", "0")], [("int8.precision", "int8", "int4")],
                        [("population.unit", "photo", "image")], [("calib", "", "unknown")], [], [], [("population.unit", "", "image")], [], [("image", "", "each run")]], said
        u = m3_docs[tier]["findings"]["c20"]["unit_amended"]
        assert (u["was"], u["now"], u["amendment"]) == ("photo", "image", "e8:am:10")
        # making wd optional changed no value
        assert e8["amendments"][10]["line"] == "amended after the lock: setting wd: required -> optional"

def test_a_cited_findings_receipt_and_source(m3_docs):
    """c2's receipt rows say when it was declared, its experiment and role, what it reads, the cited number, how it was
    read and the file it cites, with no writer; `cited` is that file's line, quote, commit and hash."""
    for tier in ("live", "owner"):
        f = m3_docs[tier]["findings"]["c2"]
        rows = [r[0] for r in f["receipt_rows"]]
        assert rows == ["declared", "experiment", "metric", "reference", "method", "source", "question link"]
        said = dict((r[0], r[1]) for r in f["receipt_rows"])
        assert (said["experiment"], said["metric"], said["reference"], said["method"]) == ("e1 · primary · cited", "fp32.top1", "76.13 ± 0.005",
                                                                                          "mean, t interval · 95% interval")
        c = f["cited"]
        assert (c["path"], c["line"], c["quote"]) == ("README.md", 3, "The published fp32 top-1 is 76.13.")
        assert re.fullmatch(r"[0-9a-f]{40}", c["commit"]) and re.fullmatch(r"[0-9a-f]{64}", c["sha256"])
        assert said["source"] == f"README.md:3 · read at commit {c['commit'][:7]} · sha256 {c['sha256'][:12]}"
        assert f["check_band"] == {"comparator": "within", "target": 76.13, "tolerance": 0.005}
        assert m3_docs[tier]["findings"]["c1"]["check_band"] == {"comparator": "at least", "target": -1, "tolerance": None}
        # no worse than 0.3 where higher is better: the worse bound at least -0.3
        assert m3_docs[tier]["findings"]["c15"]["check_band"] == {"comparator": "at least", "target": -0.3, "tolerance": None}
        assert m3_docs[tier]["findings"]["c1"]["cited"] is None
        assert [r[1] for r in m3_docs[tier]["findings"]["c1"]["receipt_rows"] if r[0] == "experiment"] == ["e1 · primary"]


def test_lead_tie_goes_to_the_interval(m3_docs):
    """q2's two interpretations each rest on one criterion: the one read with an interval leads, though the point only
    was declared first and rests on more units; the other is one more. q4's both read with an interval: the one on more
    units leads."""
    for tier in ("live", "owner"):
        qs = {q["id"]: q for q in m3_docs[tier]["questions"]}
        q2 = qs["q2"]
        assert [g["text"] for g in q2["overview"]["interpretations"]] == ["INT8 keeps most classes", "INT8 keeps every class"]
        assert m3_docs[tier]["findings"]["c4"]["reading"]["n"] > m3_docs[tier]["findings"]["c5"]["reading"]["n"]
        assert q2["lead"] == {"kind": "interpretation", "index": 0, "text": "INT8 keeps most classes", "active": True, "confirmed": True}
        assert q2["more"] == 1
        assert [g["text"] for g in qs["q4"]["overview"]["interpretations"]] == ["INT8 keeps the hardest classes", "INT8 keeps the hard classes"]



def test_a_computed_comparison_leads_and_its_limit_names_it(m3_docs):
    """q1's declared reversal is rb's computed comparison, which leads its card; the limit it raises names it by its
    place among the relations; every other limit names none."""
    for tier in ("live", "owner"):
        [q1] = [q for q in m3_docs[tier]["questions"] if q["id"] == "q1"]
        rels = q1["overview"]["relations"]
        assert rels and q1["lead"] == {"kind": "relation", "index": 0, "text": rels[0]["summary"], "active": True, "confirmed": True}
        assert q1["more"] == len(rels) + len(q1["overview"]["interpretations"]) - 1
        limits = {l["label"]: l["relations"] for l in q1["overview"]["limits"]}
        assert limits.pop("Reversal observed; repeatability uncertain") == [0], q1["overview"]["limits"]
        assert limits and all(v == [] for v in limits.values())


def test_what_leads_and_what_never_does(tmp_path):
    """q1: an active interpretation on one criterion leads a conditional one on two (a gate missed and a rule
    undetermined); q2: the user's words lead the same words a session declared on more units; q3: a relation rb cannot
    read never leads, its question's interpretation does; q4, with that relation alone, has nothing to lead; q5: the
    user's words read as conditional never move before a session's same words read as active."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    for q in ("q1", "q2", "q3"):
        rb(you, "question", "add", f"Does INT8 cost top-1, {q}?", "--id", q)
    rule = ["--metric", "change.top1", "--at-least"]
    seeds = {"e1": [(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], "e2": [(75.69, 75.53), (75.71, 75.58), (75.66, 75.48), (75.70, 75.52)],
             "e3": [(75.6, 75.9), (75.7, 75.6), (75.6, 76.1)]}
    for e in seeds:
        rb(you, "experiment", "add", f"int8 against fp32, {e}", "--id", e, "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
        rb(you, "spec", "set", e, "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 gains top-1", "-e", "e1", "-q", "q1", *rule, "0", "--role", "gate", "--when-met", "worth more seeds", "--id", "g1")
    rb(you, "criterion", "add", "INT8 keeps top-1 on nine seeds", "-e", "e1", "-q", "q1", *rule, "-1", "--min-n", "9", "--when-met", "worth more seeds", "--id", "c2")
    rb(you, "criterion", "add", "INT8 keeps top-1", "-e", "e1", "-q", "q1", *rule, "-1", "--when-met", "INT8 is fine", "--id", "c3")
    rb(you, "criterion", "add", "INT8 keeps top-1 on three seeds", "-e", "e1", "-q", "q2", *rule, "-1", "--when-met", "INT8 is safe to ship", "--id", "c4")
    rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 on four seeds", "-e", "e2", "-q", "q2", *rule, "-1", "--when-met", "INT8 is safe to ship", "--id", "c5")
    rb(you, "criterion", "add", "INT8 keeps top-1 on e3", "-e", "e3", "-q", "q3", *rule, "-1", "--when-met", "INT8 keeps it", "--id", "c6")
    rb(you, "criterion", "add", "INT8 keeps top-1 on e2", "-e", "e2", "-q", "q3", *rule, "-1", "--id", "c7")
    rb(you, "criterion", "relate", "c6", "reverses", "c7", "--orientation", "same", "--why", "the same comparison")
    rb(you, "question", "add", "Does INT8 cost top-1, q5?", "--id", "q5")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a tenth", "-e", "e1", "-q", "q5", *rule, "-0.1", "--when-met", "INT8 is safe to ship", "--id", "c10")
    rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 within a point, q5", "-e", "e1", "-q", "q5", *rule, "-1", "--when-met", "INT8 is safe to ship", "--id", "c11")
    rb(you, "question", "add", "Does INT8 cost top-1, q4?", "--id", "q4")
    rb(you, "criterion", "add", "INT8 keeps top-1 on e3, q4", "-e", "e3", "-q", "q4", *rule, "-1", "--id", "c8")
    rb(you, "criterion", "add", "INT8 keeps top-1 on e1, q4", "-e", "e1", "-q", "q4", *rule, "-1", "--id", "c9")
    rb(you, "criterion", "relate", "c8", "reverses", "c9", "--orientation", "same", "--why", "the same comparison")
    files = []
    for e, pairs in seeds.items():
        for seed, (fp32, int8) in enumerate(pairs, 1):
            files.append(rec.json(you, "run", "add", e, f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")["data"]["object"]["id"])
    # one of e3's runs edited by hand: the reversal cannot be read
    [run_file] = list((root / ".rb" / "experiments" / "e3" / "runs").glob(f"{files[-1]}.yaml"))
    run_file.write_text(run_file.read_text(encoding="utf-8").replace("76.1", "76.2"), encoding="utf-8")
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(root, tmp_path).items():
        qs = {q["id"]: q for q in doc["questions"]}
        assert (qs["q1"]["lead"]["text"], qs["q1"]["lead"]["active"]) == ("INT8 is fine", True), qs["q1"]["overview"]["interpretations"]
        assert [(g["text"], g["confirmed"]) for g in qs["q2"]["overview"]["interpretations"]] == [("INT8 is safe to ship", True), ("INT8 is safe to ship", False)]
        assert qs["q2"]["lead"]["confirmed"] is True
        assert [r["outcome"] for r in qs["q3"]["overview"]["relations"]] == ["integrity"]
        assert (qs["q3"]["lead"]["kind"], qs["q3"]["lead"]["text"], qs["q3"]["more"]) == ("interpretation", "INT8 keeps it", 1)
        # q4 holds that relation alone: nothing leads, and it is one more
        assert (qs["q4"]["lead"], qs["q4"]["more"]) == (None, 1)
        # q5: the user's words, missed, are conditional; the session's same words, met, lead as not confirmed
        assert [(g["active"], g["confirmed"]) for g in qs["q5"]["overview"]["interpretations"]] == [(True, False), (False, True)]
        assert (qs["q5"]["lead"]["active"], qs["q5"]["lead"]["confirmed"]) == (True, False)


def test_an_amendment_reads_its_record_when_the_log_was_rewritten(tmp_path):
    """A log line's value rewritten by hand is not what e1's amendment says, and with the line gone no amendment pairs
    with one: each change is read from the amendment itself, and making lr optional changes no value."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "criterion", "add", "INT8 keeps top-1", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    rb(you, "spec", "set", "e1", "lr", "0.1", "--no-verify")
    rb(you, "freeze", "e1", "-m", "before the runs")
    rb(you, "spec", "set", "e1", "lr", "0.1", "--optional", "--no-verify", "--amend", "--why", "lr may go unrecorded")
    rb(you, "spec", "set", "e1", "population.unit", "photo", "--no-verify", "--amend", "--why", "the photos are the unit")
    log = root / ".rb" / "log.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert '"value":"photo"' in lines[-1].replace(" ", "")
    want = [("amended after the lock: setting lr: required -> optional", []),
            ("unit amended after the lock: run -> photo", [{"setting": "population.unit", "was": "", "now": "photo"}])]
    # its value rewritten; then the line gone, so no amendment pairs with a line
    for n, edited in enumerate([lines[:-1] + [lines[-1].replace('"photo"', '"FAKE"', 1)], lines[:-1]]):
        log.write_text("\n".join(edited) + "\n", encoding="utf-8")
        (tmp_path / f"dumps{n}").mkdir()
        for doc in both_tiers(root, tmp_path / f"dumps{n}").values():
            [e1] = doc["experiments"]
            assert [(a["line"], a["changes"]) for a in e1["amendments"]] == want, n

def test_interpretation_says_whether_its_criteria_hold(m3_docs):
    """Each interpretation's hold is the screens' words for the rules it rests on, counted: one met, a point only met
    on its estimate, two whose words differ."""
    for tier in ("live", "owner"):
        held = {g["text"]: g["holds"] for q in m3_docs[tier]["questions"] for g in q["overview"]["interpretations"]}
        assert held == {"INT8 keeps most classes": "Its rule is met.", "INT8 keeps every class": "Its rule is met on its estimate.",
                        "INT8 keeps the hard classes": "Its rule is met.", "INT8 keeps the hardest classes": "Its rule is met.",
                        "INT8 keeps its latency gain": "Its 2 rules: 1 met · 1 estimate meets its rule."}


def test_a_session_interpretation_says_not_confirmed(m3_docs, m3, monkeypatch):
    """q3's interpretation, which a session declared, leads q3 as not confirmed, in its group, in what rests on its
    criteria and on rb ui's home; the user's own on q2 is confirmed."""
    for tier in ("live", "owner"):
        doc = m3_docs[tier]
        [q3] = [q for q in doc["questions"] if q["id"] == "q3"]
        assert q3["lead"] == {"kind": "interpretation", "index": 0, "text": "INT8 keeps its latency gain", "active": True, "confirmed": False}
        assert [g["confirmed"] for g in q3["overview"]["interpretations"]] == [False]
        assert [i["confirmed"] for i in doc["findings"]["c6"]["rests_on"]["interpretations"]] == [False]
    at_the_record(monkeypatch, m3)
    briefs = {b.id: b for b in home_view().briefs}
    assert (briefs["q3"].overview["lead"]["confirmed"], briefs["q2"].overview["lead"]["confirmed"]) == (False, True)


def test_an_experiments_short_title(m3_docs, m3, monkeypatch):
    """An experiment's short title is its title up to its first ": " where that leaves 8 characters or more, else the
    whole title, in the document and on rb ui's home."""
    for tier in ("live", "owner"):
        short = {x["id"]: x["short_title"] for x in m3_docs[tier]["experiments"]}
        assert (short["e1"], short["e2"], short["e3"]) == ("int8 against fp32", "calibration set size", "e3: per-photo reading")
    at_the_record(monkeypatch, m3)
    home = {e.id: e.short_title for b in home_view().briefs for e in b.experiments}
    assert home["e2"] == "calibration set size"


def runs_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, for a run's own fields (BP 1b-2), recorded on the branch feature-ana: e1, locked,
    with runs whose commands are given at the time (r_given), watched by rb journal run (recorded by rb reconcile
    --apply), given later for a run made in May (r_later), and none at all (r_none, a session's, reading the held-out
    set and making a checkpoint); its numbers carry many digits."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "README.md").write_text("# INT8 ResNet-50\n", encoding="utf-8")
    rec.git("add", "README.md")
    rec.git("commit", "-q", "-m", "readme")
    rec.git("checkout", "-q", "-b", "feature-ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    rb(you, "criterion", "add", "INT8 gains top-1", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-5", "--role", "gate", "--id", "g1")
    rb(you, "resource", "add", "holdout", "--type", "dataset", "--name", "ImageNet held-out images", "--url", "https://data.example.org/holdout")
    rb(you, "resource", "add", "int8-ckpt", "--type", "checkpoint", "--name", "ResNet-50 INT8")
    rb(you, "freeze", "e1", "-m", "before the runs")
    ids = {}
    ids["r_given"] = rec.json(you, "run", "add", "e1", "fp32.top1=75.68666666666667", "int8.top1=75.53333333333333", "--set", "seed=1",
                              "--command", "python eval.py --seed 1")["data"]["object"]["id"]
    (root / "eval.py").write_text("import json\nprint(json.dumps({'metrics': {'fp32.top1': 75.71, 'int8.top1': 75.58}}))\n", encoding="utf-8")
    rb(you, "journal", "run", "--", sys.executable, "eval.py", "--seed", "2")
    rb(you, "reconcile", "--apply")
    ids["r_later"] = rec.json(you, "run", "add", "e1", "fp32.top1=75.66", "int8.top1=75.48", "--set", "seed=3", "--produced-at", "2026-05-07T20:07:00+00:00",
                              "--command", "python eval.py --seed 3")["data"]["object"]["id"]
    ids["r_none"] = rec.json(CLAUDE_CODE, "run", "add", "e1", "fp32.top1=75.7", "int8.top1=75.52", "--set", "seed=4", "--input", "holdout:eval",
                             "--output", "int8-ckpt:checkpoint")["data"]["object"]["id"]
    # a session says its model later on: a line of rb's own, no write
    rb({**CLAUDE_CODE, "RB_TEST_NOW": "2030-01-01T00:00:00.000+00:00"}, "context", "--model", "claude-opus-5-5")
    rec.ids.update(ids)
    return rec


@pytest.fixture(scope="module")
def m4(tmp_path_factory):
    return runs_record(tmp_path_factory.mktemp("shell-runs") / "int8")


@pytest.fixture(scope="module")
def m4_docs(m4, tmp_path_factory):
    return both_tiers(m4.root, tmp_path_factory.mktemp("runs-dumps"))


def test_metrics_say_each_number_as_recorded(m4_docs, m4):
    """A run's numbers as rb show says them, each arm's after its name: every digit recorded, never rounded (a row
    must not read 1234.57 beside a rule of 1234.5678)."""
    for tier in ("live", "owner"):
        r = m4_docs[tier]["runs"][m4.ids["r_given"]]
        assert r["metrics_text"] == "fp32: top1=75.68666666666667 · int8: top1=75.53333333333333"
        assert r["label"] == "fp32 and int8 · seed 1" and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d UTC", r["at"])


def test_command_kinds(m4_docs, m4):
    """A run's command and how rb knows it: given at the time, watched by rb journal run (the only one read as
    observed), given later for a run made earlier, or not recorded."""
    for tier in ("live", "owner"):
        runs = m4_docs[tier]["runs"]
        kinds = {rid: r["command"]["kind"] for rid, r in runs.items()}
        assert kinds[m4.ids["r_given"]] == "as given" and kinds[m4.ids["r_later"]] == "given later" and kinds[m4.ids["r_none"]] == "not recorded"
        [watched] = [rid for rid, k in kinds.items() if k == "observed"]
        assert "eval.py --seed 2" in runs[watched]["command"]["text"]
        assert runs[m4.ids["r_given"]]["command"]["text"] == "python eval.py --seed 1" and runs[m4.ids["r_none"]]["command"]["text"] == ""
        rows = [r[1] for r in runs[m4.ids["r_later"]]["receipt_rows"] if r[0] == "command"]
        assert rows[0] == "command given later, not observed: python eval.py --seed 3" and rows[1].startswith("how rb knows: it was made before it was recorded")


def test_receipt_rows_by_tier(m4_docs, m4):
    """A run's receipt rows are its card's own lines by field, the lock said as locked; no writer, session, branch or
    worktree on any tier; the machine's platform on the Live tier alone, as a row of its own."""
    for tier in ("live", "owner"):
        for rid, r in m4_docs[tier]["runs"].items():
            fields = [row[0] for row in r["receipt_rows"]]
            said = " ".join(row[1] for row in r["receipt_rows"])
            assert ("platform" in fields) == (tier == "live"), (tier, fields)
            for word in ("ana", "human:", "agent", "claude", "sess-", "feature-", "worktree"):
                assert word not in said.lower(), (word, said)
            basis = [row[1] for row in r["receipt_rows"] if row[0] == "basis"]
            assert basis and basis[0].endswith("· spec locked")
            env = [row[1] for row in r["receipt_rows"] if row[0] == "environment"]
            assert env and env[0].startswith("python ") and "Linux" not in env[0] and "Darwin" not in env[0]
        rows = m4_docs[tier]["runs"][m4.ids["r_none"]]["receipt_rows"]
        assert ["inputs", "read: holdout (eval) · reconstructible when recorded", ["holdout"]] in rows
        assert [r for r in rows if r[0] == "outputs"][0][2] == ["int8-ckpt"]


def test_made_counts_the_record(m4_docs, m4, gates_docs, calls_docs, monkeypatch):
    """Home's counts and the line on how the record was made are counted from it: its criteria declared after their
    evidence existed (both, the May run being older), its runs that record their command as given (two of four), the
    calls waiting on you as Your call counts them, and its writes, in the document and on rb ui's home; the line's
    other forms on the other records."""
    st = m4.json(Writers.HUMAN, "status")["data"]
    log = [json.loads(x) for x in (m4.root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    writes = [x for x in log if x.get("op") != "session"]
    for tier in ("live", "owner"):
        home = m4_docs[tier]["home"]
        assert home["counts"] == {"questions": 1, "experiments": 1, "criteria": 2, "runs": 4, "retracted": 0, "analyses": 0, "resources": 2,
                                  "calls": m4_docs[tier]["your_call"]["n"], "writes": len(writes)}
        # the run made in May came before both criteria
        assert home["made"] == {"criteria": 2, "after_evidence": 2, "runs": 4, "with_command": 2,
                                "line": "All 2 rules were written after their results came in · 2 of 4 runs record their command (as given)"}
        assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d UTC", home["last_write"]["at"]) and not home["last_write"]["at"].startswith("2030")
    at_the_record(monkeypatch, m4)
    h = home_view()
    assert (h.counts["runs"], h.made["with_command"], h.last_write) == (4, 2, m4_docs["owner"]["home"]["last_write"]["at"])
    assert st["counts"]["run_count"] == 4 and any(x.get("op") == "session" for x in log)
    assert calls_docs["owner"]["home"]["counts"]["calls"] == calls_docs["owner"]["your_call"]["n"] == 12
    assert gates_docs["owner"]["home"]["made"]["line"] == "No rule was written after its results came in · no run records its command (as given)"
    assert re.fullmatch(r"2 of \d+ rules were written after their results came in · no run records its command \(as given\)",
                        calls_docs["owner"]["home"]["made"]["line"])



def test_a_log_in_a_nested_worktree_is_named_as_each_tier_names_a_path(tmp_path):
    """A run whose command a log line in a worktree nested in the checkout records: the page names that log by its
    path inside the worktree, as it names any file there, never by the worktree's place, with its line and where rb's
    words name it alone; the made line in the singular, for one criterion and one run, and for one of two."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "README.md").write_text("# INT8 ResNet-50\n", encoding="utf-8")
    rec.git("add", "README.md")
    rec.git("commit", "-q", "-m", "readme")
    rec.git("worktree", "add", "-q", ".worktrees/b", "-b", "exp-b")
    (root / ".worktrees" / "b" / "launch.log").write_text("python eval.py --seed 7\n", encoding="utf-8")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    produced = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 600))
    run = rec.json(you, "run", "add", "e1", "fp32.top1=75.73", "int8.top1=75.54", "--produced-at", produced, "--command", "python eval.py --seed 7",
                   "--command-source", ".worktrees/b/launch.log:1")["data"]["object"]["id"]
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    (tmp_path / "first").mkdir()
    assert both_tiers(root, tmp_path / "first")["owner"]["home"]["made"]["line"] == (
        "The one rule was written after its results came in · the one run records its command (as given)")
    # e2's c2, which no run reads yet, is declared before any evidence of its own
    rb(you, "experiment", "add", "int8 against fp32, per channel", "--id", "e2", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "criterion", "add", "INT8 per channel keeps top-1", "-e", "e2", "--metric", "change.top1", "--at-least", "-1", "--id", "c2")
    # a run made in May, whose log was written today: rb's words say when that log was written, naming it
    may = rec.json(you, "run", "add", "e1", "fp32.top1=75.70", "int8.top1=75.50", "--produced-at", "2026-05-07T20:07:00+00:00", "--command",
                   "python eval.py --seed 7", "--command-source", ".worktrees/b/launch.log:1", "--again", "--why", "the May run")["data"]["object"]["id"]
    # the log named by its whole path on this machine: kept from the record's folder, as every path the record keeps
    whole = rec.json(you, "run", "add", "e1", "fp32.top1=75.71", "int8.top1=75.51", "--produced-at", produced, "--command", "python eval.py --seed 7",
                     "--command-source", f"{root.resolve()}/.worktrees/b/launch.log:1", "--again", "--why", "the same launch")["data"]["object"]["id"]
    (tmp_path / "dumps").mkdir()
    docs = both_tiers(root, tmp_path / "dumps")
    for tier, doc in docs.items():
        said = json.dumps(doc["runs"][whole]["receipt_rows"])
        assert "launch.log:1" in said and str(root.resolve()) not in said and (".worktrees" in said) == (tier == "live"), (tier, said)
        r = doc["runs"][run]
        assert r["command"]["kind"] == "logged"
        [how] = [row[1] for row in r["receipt_rows"] if row[1].startswith("how rb knows")]
        assert "launch.log:1" in how and (".worktrees" in how) == (tier == "live"), (tier, how)
        said = json.dumps(doc["runs"][may]["receipt_rows"])
        assert "launch.log" in said and (".worktrees" in said) == (tier == "live"), (tier, said)
        assert doc["home"]["made"]["line"] == "1 of 2 rules was written after its results came in · 2 of 3 runs record their command (as given)"
    assert docs["owner"]["runs"][run]["receipt_rows"] and "launch.log:1 (in another worktree) records this launch" in json.dumps(docs["owner"]["runs"][run]["receipt_rows"])

def test_a_gate_met_draws_neutral(m4_docs):
    """g1, a gate met across its interval, is policy, not a finding: never drawn green (D4); c1, met the same way, is."""
    for tier in ("live", "owner"):
        fs = m4_docs[tier]["findings"]
        assert fs["g1"]["standing"].startswith("gate met") and (fs["g1"]["good"], fs["c1"]["good"]) == (False, True)


def test_a_check_of_a_setting_is_said_as_what_rb_found(int8_demo, tmp_path):
    """The INT8 demo checks its settings' sources: History's log rows say each check as rb found the source (k of n
    settings checked as stated, or one setting's), never with a status word that reads as a claim."""
    root = Path(getattr(int8_demo, "root", int8_demo))
    for tier in ("live", "owner"):
        rows = [r for r in contract.model_document(root, tier, tmp_path)["history"]["log"] if r["op"] == "spec_verify"]
        assert rows and all("verified" not in r["sentence"] for r in rows), rows
        assert all(re.search(r": (\d+ of \d+ settings? checked as stated|\S+: (checked as stated|its source states another value|its source no longer states it|provisional))$",
                             r["sentence"]) for r in rows), rows


def test_home_within_budget(scale_record, monkeypatch):
    """The bridge's home op (what rb ui reads on every change) and rb open's /state.json stay within 20% of phase 0's
    figures for this system (tests/shell_baselines.py, for_this_machine) on the scale record, in size and in time, time
    read against rb status --json on the same record and machine so a faster or slower machine holds the same ratio. The three are read in turn, round
    by round, so other work on the machine weighs on each alike; the median of the rounds' ratios is held."""
    import statistics
    import shell_baselines
    root, then = Path(getattr(scale_record, "root", scale_record)).resolve(), shell_baselines.for_this_machine()
    as_the_user(monkeypatch)
    server = Open(root)
    rounds = []
    try:
        def state() -> int:
            with urllib.request.urlopen(server.url + "/state.json", timeout=120) as r:
                return len(r.read())

        def timed(read) -> tuple:
            start = time.perf_counter()
            size = read()
            return time.perf_counter() - start, size

        for _ in range(6):
            rounds.append({"home": timed(lambda: shell_baselines.home_op(root)), "status": timed(lambda: shell_baselines.status_json(root)), "state_json": timed(state)})
    finally:
        server.close()
    rounds = rounds[1:]
    for k in ("home", "state_json"):
        size = rounds[0][k][1]
        assert size <= 1.2 * then[k]["bytes_median"], (k, size, then[k]["bytes_median"])
        ratio = statistics.median(r[k][0] / r["status"][0] for r in rounds)
        was = then[k]["seconds_median"] / then["status"]["seconds_median"]
        cap = 1.2 * was
        assert ratio <= cap, (k, round(ratio, 3), round(cap, 3))


def test_rb_open_s_document_carries_its_places_on_the_shell(tmp_path, monkeypatch):
    """rb open's document says where it shows the record as places.rs says it on the shell (BP 4b): at the live start
    place no scope bar and Elsewhere's row for the other worktree; at a past version the scope bar says it, read only,
    with rb's reason for offering no command, and the places are still the clone's, listed from where rb open started,
    never from rb's checkout of that version."""
    import rbtest
    as_the_user(monkeypatch)
    root = tmp_path / "int8"
    root.mkdir()
    git = lambda *argv: subprocess.run(["git", "-c", "user.name=ana", "-c", "user.email=ana@example.org", *argv], cwd=root, check=True, capture_output=True)
    rb = lambda *argv: subprocess.run([*rbtest.command(), *argv], cwd=root, env=rbtest.environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    git("init", "-q", "-b", "main")
    assert rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents").returncode == 0
    assert rb("question", "add", "Does INT8 cost accuracy on ImageNet?", "--id", "q-int8").returncode == 0
    git("add", "-A")
    git("commit", "-q", "-m", "the record")
    git("worktree", "add", "-q", "-b", "exp-b", ".worktrees/b")
    assert rb("note", "add", "the held-out set is ImageNet validation").returncode == 0
    git("add", "-A")
    git("commit", "-q", "-m", "a note")
    dump = tmp_path / "doc.json"
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    monkeypatch.setenv("RB_TEST_SHELL_DUMP", str(dump))
    server = Open(root)
    try:
        def doc(query: str = "") -> dict:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(server.url + "/" + query, timeout=60) as r:
                assert r.status == 200
            return json.loads(dump.read_text(encoding="utf-8"))
        live = doc()["places"]
        past = doc(f"?at={live['versions'][-1]['sha']}")["places"]
    finally:
        server.close()
    assert live["scope_bar"] is None and live["place"]["start"] is True
    assert [p["label"] for p in live["places"]] == [p["label"] for p in past["places"]] and len(live["places"]) == 2
    assert live["elsewhere"] and all("exp-b" in json.dumps(e) for e in live["elsewhere"]), live["elsewhere"]
    assert past["place"]["read_only"] is True and past["scope_bar"]["back"] == "Back to now"
    assert past["scope_bar"]["line"].endswith("read only") and past["scope_bar"]["withheld"].startswith("A past version offers no command")
    assert past["elsewhere"] == []


def r7_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet as the R7 fields read it: c1 declares what meeting it would mean for q1 and the user
    accepts it with their words; e2 extends e1 with a note; e1's lr is read from its config and seed is per run; e3
    reads two widths as fixed design cells, three seeds in each, so each cell has an interval; e4 is filed under no
    question; c2 misses its rule and no call on it is recorded."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    (root / "configs").mkdir()
    (root / "configs" / "train.yaml").write_text("optim:\n  lr: 0.1\n", encoding="utf-8")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    for e, filed in (("e1", True), ("e2", True), ("e3", True), ("e4", False)):
        rb(you, "experiment", "add", f"int8 against fp32, {e}", "--id", e, *(("-q", "q1") if filed else ()), "--baseline", "fp32", "--candidate", "int8")
        rb(you, "spec", "set", e, "seed", "--per-run")
    rb(you, "spec", "set", "e1", "optim.lr", "--source", "configs/train.yaml#optim.lr")
    rb(you, "experiment", "relate", "e2", "extends", "e1", "--note", "the same model at INT4")
    rb(you, "spec", "set", "e3", "width", "--design-factor")
    rule = ["--metric", "change.top1", "--at-least"]
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "-q", "q1", *rule, "-1", "--when-met", "INT8 is accurate enough to ship", "--id", "c1")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a hundredth of a point", "-e", "e1", "-q", "q1", *rule, "-0.01", "--id", "c2")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point at each width", "-e", "e3", "-q", "q1", *rule, "-1", "--id", "c3")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point, not filed", "-e", "e4", *rule, "-1", "--id", "c4")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        for e in ("e1", "e4"):
            rb(you, "run", "add", e, f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
        for width, d in ((256, 0.0), (512, 0.2)):
            rb(you, "run", "add", "e3", f"fp32.top1={fp32}", f"int8.top1={int8 - d}", "--set", f"seed={seed}", "--set", f"width={width}")
    rb(you, "decide", "c1", "accept", "--why", "within a point on three seeds", "--words", "ship it once seed 3 lands")
    return rec


@pytest.fixture(scope="module")
def r7(tmp_path_factory):
    return r7_record(tmp_path_factory.mktemp("shell-r7") / "int8")


@pytest.fixture(scope="module")
def r7_docs(r7, tmp_path_factory):
    return both_tiers(r7.root, tmp_path_factory.mktemp("r7-dumps"))


def experiment_of(doc: dict, eid: str) -> dict:
    [e] = [e for e in doc["experiments"] if e["id"] == eid]
    return e


def test_an_interpretation_says_the_call_on_its_criterion(r7_docs):
    """c1's interpretation on q1 says the user's call on c1 beside it, with their words on rb open and without them on
    a page."""
    for tier, words in (("live", True), ("owner", False)):
        [q] = [q for q in r7_docs[tier]["questions"] if q["id"] == "q1"]
        [group] = [g for g in q["overview"]["interpretations"] if "c1" in g["criteria"]]
        [call] = group["calls"]
        assert re.match(r"Call on c1: accepted \d{4}-\d\d-\d\d: within a point on three seeds", call), call
        assert ("ship it once seed 3 lands" in call) is words, (tier, call)


def test_an_experiment_says_its_relations_settings_and_rule_counts_as_its_card(r7, r7_docs, monkeypatch):
    """e2's relation and e1's settings in the card's own lines, and e1's rules counted as rb status counts them."""
    at_the_record(monkeypatch, r7)
    for tier in ("live", "owner"):
        doc = r7_docs[tier]
        e1, e2 = experiment_of(doc, "e1"), experiment_of(doc, "e2")
        assert e2["relation_lines"] == ["extends: e1 (the same model at INT4)"] and e1["relation_lines"] == []
        assert e1["settings_line"] in object_card("e1").facts and e1["settings_line"] == "settings: 1 per-run · 1 verified"
        # in rb status's words for its tally, as the home's tally says the record's
        assert dict(map(tuple, e1["tally"])) == {"meets": 1, "misses": 1} and e1["muted"] == []
        assert experiment_of(doc, "e4")["tally"] == [["meets", 1]]


def test_a_cell_of_the_comparison_table_keeps_what_rb_compare_says_of_it(r7, r7_docs):
    """Each cell keeps rb compare's change against the baseline and what it rests on; the baseline's has none."""
    cmp = r7.json(Writers.HUMAN, "compare", "e1")["data"]
    want = {r["variant"]: r["metrics"]["top1"] for r in cmp["rows"]}
    for tier in ("live", "owner"):
        rows = {r["variant"]: r["metrics"]["top1"] for r in experiment_of(r7_docs[tier], "e1")["table"]["rows"]}
        fp32, int8 = rows["fp32"], rows["int8"]
        assert (fp32["delta"], fp32["better"], fp32["paired"], fp32["said"], fp32["basis"], fp32["differs_in"], fp32["other_code"]) == (None, None, False, "", "", [], "")
        assert (int8["delta"], int8["better"], int8["paired"], int8["primary"]) == (want["int8"]["delta"], False, True, want["int8"].get("primary", False))
        assert int8["said"] == want["int8"].get("said", "") and int8["basis"] == want["int8"].get("basis", "")


def grid_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, by the user, for a grid of several columns: e1 varies precision (fp32 baseline,
    int8 and int4 candidates) over calibration sets of 128 and 512 images, a fixed design factor. At 128 images fp32
    and int8 share three seeds, one run each holding both arms; at 512 images fp32 has seed 1 and int8 seeds 2 and 3,
    each a run of its own, so they never pair; int4 is run at 512 images alone, at batch 128 where the others are at
    256, which e1 does not declare it varies. c1 reads int8 against fp32 at 128 images, c2 at each size.
    e2: three runs of fp32 and three of int8, each a run of its own, so they pair by no unit; c3 reads them.
    e3: three seeds of both arms, and a second execution of seed 1 that repeats it; c4 reads them.
    e4: a calibration size alone per run, two runs at each, so each size's two runs are one unit."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "precision against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8", "--candidate", "int4",
       "--varies", "precision")
    for v in ("fp32", "int8", "int4"):
        rb(you, "spec", "set", "e1", f"{v}.precision", v, "--no-verify")
        rb(you, "spec", "set", "e1", f"{v}.batch", "128" if v == "int4" else "256", "--no-verify")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "spec", "set", "e1", "calib", "--design-factor")
    pair = ["--metric", "top1", "--subject", "int8", "--reference", "fp32", "--at-least", "-1"]
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point of FP32 at 128 images", "-e", "e1", "-q", "q1", *pair, "--where", "calib=128", "--id", "c1")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point of FP32 at every calibration size", "-e", "e1", "-q", "q1", *pair, "--each", "--id", "c2")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        rb(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}", "--set", "calib=128")
    rb(you, "run", "add", "e1", "fp32.top1=75.80", "--set", "seed=1", "--set", "calib=512")
    for seed, int8 in ((2, 75.60), (3, 75.64)):
        rb(you, "run", "add", "e1", f"int8.top1={int8}", "--set", f"seed={seed}", "--set", "calib=512")
    rb(you, "run", "add", "e1", "int4.top1=74.10", "--set", "seed=1", "--set", "calib=512")
    rb(you, "experiment", "add", "int8 against fp32, arms run apart", "--id", "e2", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point, arms run apart", "-e", "e2", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
       "--id", "c3")
    for fp32, int8 in ((75.69, 75.53), (75.71, 75.58), (75.66, 75.48)):
        rb(you, "run", "add", "e2", f"fp32.top1={fp32}")
        rb(you, "run", "add", "e2", f"int8.top1={int8}")
    rb(you, "experiment", "add", "int8 against fp32, a seed run twice", "--id", "e3", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e3", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point, a seed run twice", "-e", "e3", "-q", "q1", "--metric", "change.top1", "--at-least",
       "-1", "--id", "c4")
    first = None
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        made = rec.json(you, "run", "add", "e3", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
        first = first or made["data"]["object"]["id"]
    rb(you, "run", "add", "e3", "fp32.top1=75.70", "int8.top1=74.90", "--set", "seed=1", "--repeats", first)
    rb(you, "experiment", "add", "int8 against fp32 by calibration size", "--id", "e4", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e4", "calib", "--design-factor")
    for calib in (128, 512):
        for fp32, int8 in ((75.69, 75.53), (75.71, 75.58)):
            rb(you, "run", "add", "e4", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"calib={calib}")
    return rec


@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    rec = grid_record(tmp_path_factory.mktemp("shell-grid") / "grid")
    return rec, both_tiers(rec.root, tmp_path_factory.mktemp("grid-dumps"))


def grid_cells(doc: dict, eid: str = "e1") -> dict:
    """{(variant, the settings of its column): its top1 cell} of `eid`'s table."""
    t = experiment_of(doc, eid)["table"]
    return {(r["variant"], tuple(sorted({**r["where"], **r["unvaried"]}.items()))): r["metrics"]["top1"] for r in t["rows"]}


def test_a_grid_of_one_column_says_its_runs_and_the_changes_interval(int8, docs):
    """INT8 against FP32 on three seeds, one column: each cell its runs, by id, and "3 seeds"; int8's change against
    fp32 with rb's t interval over the seeds' differences, the same as c1's own reading, and c1 as what reads it."""
    want = int8.json(Writers.HUMAN, "show", "c1")["data"]["finding"]["comparison"]
    runs = {r["id"] for r in int8.json(Writers.HUMAN, "status")["data"]["runs"]}
    for tier in ("live", "owner"):
        t = experiment_of(docs[tier], "e1")["table"]
        assert t["unit"] == "seed" and t["conditions"] == [], t
        rows = {r["variant"]: r["metrics"]["top1"] for r in t["rows"]}
        fp32, i8 = rows["fp32"], rows["int8"]
        for c in (fp32, i8):
            assert (c["runs"], c["count_said"], set(c["run_ids"]) <= runs, len(c["run_ids"])) == (3, "3 seeds", True, 3), c
        assert (fp32["lo"], fp32["hi"], fp32["no_interval"], fp32["criteria"]) == (None, None, "", []), fp32
        assert i8["lo"] == pytest.approx(want["lo"]) and i8["hi"] == pytest.approx(want["hi"]) and i8["level"] == 0.95, (i8, want)
        assert (i8["no_interval"], i8["criteria"], i8["in_column"]) == ("", ["c1"], True), i8


def test_a_grid_of_several_columns_keeps_each_column_to_one_set_of_settings(grid):
    """Several columns, one with an empty cell, one with an unpaired change: at 128 images int8's change against fp32
    has its interval and c1 and c2 read it; at 512 images int8's seeds never meet fp32's, so its change has no interval
    and says why; int4's batch, which e1 does not declare it varies, keeps it out of fp32's column, so its change is no
    difference within one; int4 is not run at 128 images."""
    rec, docs = grid
    for tier in ("live", "owner"):
        t = experiment_of(docs[tier], "e1")["table"]
        assert t["conditions"] == ["calib"], t
        cells = grid_cells(docs[tier])
        col = lambda calib, batch: (("batch", batch), ("calib", calib))
        assert set(cells) == {("fp32", col("128", "256")), ("int8", col("128", "256")), ("fp32", col("512", "256")), ("int8", col("512", "256")),
                              ("int4", col("512", "128"))}, cells
        i8 = cells[("int8", col("128", "256"))]
        assert i8["paired"] and i8["lo"] is not None and i8["hi"] is not None and i8["criteria"] == ["c1", "c2"] and i8["count_said"] == "3 seeds", i8
        apart = cells[("int8", col("512", "256"))]
        assert (apart["paired"], apart["lo"], apart["hi"], apart["no_interval"], apart["count_said"], apart["runs"], apart["in_column"]) == (
            False, None, None, "seeds don't match, and 2 against 1 is too few for an interval", "2 seeds", 2, True), apart
        i4 = cells[("int4", col("512", "128"))]
        assert (i4["in_column"], i4["lo"], i4["no_interval"], i4["delta"], i4["better"]) == (
            False, None, "fp32 was not run with the same batch, so no difference here", None, None), i4
        assert cells[("fp32", col("512", "256"))]["count_said"] == "1 run", cells
        # no column mixes two values of a setting the experiment does not vary, and a difference is only within one
        for key in {k for _, k in cells}:
            for name in ("calib", "batch"):
                assert len({dict(k)[name] for _, k in cells if k == key}) == 1
        for (v, k), c in cells.items():
            if c["delta"] is not None and c["in_column"]:
                assert ("fp32", k) in cells, (v, k)


def test_a_grid_says_rbs_interval_where_the_arms_never_pair_and_no_second_one_beside_a_rules(grid):
    """e2's arms are run apart: the change has Welch's interval, the same as c3's. e3 repeats seed 1: c4 reads the
    repeat and the table leaves it out, so the cell gives no interval of its own beside c4's reading. e4's two runs at
    each size are one unit: "2 runs as 1 unit"."""
    rec, docs = grid
    c3 = rec.json(Writers.HUMAN, "show", "c3")["data"]["finding"]["comparison"]
    for tier in ("live", "owner"):
        i8 = grid_cells(docs[tier], "e2")[("int8", ())]
        assert (i8["paired"], i8["count_said"], i8["criteria"]) == (False, "3 runs", ["c3"]), i8
        assert i8["lo"] == pytest.approx(c3["lo"]) and i8["hi"] == pytest.approx(c3["hi"]), (i8, c3)
        e3 = grid_cells(docs[tier], "e3")[("int8", ())]
        assert e3["read_by"] and (e3["lo"], e3["hi"], e3["no_interval"]) == (None, None, ""), e3
        e4 = experiment_of(docs[tier], "e4")["table"]
        assert {r["metrics"]["top1"]["count_said"] for r in e4["rows"]} == {"2 runs as 1 unit"}, e4


def test_a_cells_interval_is_the_rules_that_reads_it(tmp_path):
    """One comparison, one interval. e1: int8 has seeds 1 to 4 and fp32 seeds 1 to 3, with seed 4 at int8's mean, so
    the table's means and c1's paired reading agree: the cell gives c1's interval over the three shared seeds. e2: an
    analysis reports the change with the table's own estimate and its bootstrap interval: the cell gives c2's, the
    analysis's. e3: the same partial overlap with no rule: no interval over all of them, and why."""
    rec = Writers(tmp_path / "one")
    rec.root.mkdir(parents=True)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    seeds = [(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)]
    int8_mean = sum(i for _, i in seeds) / 3
    for e in ("e1", "e2", "e3"):
        rb(you, "experiment", "add", f"int8 against fp32, {e}", "--id", e, "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
        rb(you, "spec", "set", e, "seed", "--per-run")
        if e != "e3":
            rb(you, "criterion", "add", f"INT8 keeps top-1 within a point, {e}", "-e", e, "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
               "--id", "c" + e[1])
        for seed, (fp32, int8) in enumerate(seeds, 1):
            rb(you, "run", "add", e, f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
        if e != "e2":
            rb(you, "run", "add", e, f"int8.top1={int8_mean}", "--set", "seed=4")
    delta = int8_mean - sum(f for f, _ in seeds) / 3
    rb(you, "analysis", "add", "e2", "--compare", f"int8/fp32:top1={delta!r}[-0.4,0.1]", "--method", "paired bootstrap over images", "--unit",
       "image", "--units", "50000", "--id", "a1")
    doc = dumped(rec.root, "page", tmp_path)[0]
    for e, c in (("e1", "c1"), ("e2", "c2")):
        want = rec.json(you, "show", c)["data"]["finding"]["comparison"]
        cell = grid_cells(doc, e)[("int8", ())]
        assert cell["lo"] == pytest.approx(want["lo"]) and cell["hi"] == pytest.approx(want["hi"]) and cell["criteria"] == [c], (e, cell, want)
    cell = grid_cells(doc, "e3")[("int8", ())]
    assert (cell["lo"], cell["hi"], cell["no_interval"]) == (None, None, "only 3 of the seeds match, so no interval over all of them"), cell


def test_a_design_cell_says_its_interval_and_units(r7_docs):
    """c3 reads two widths, three seeds each: each cell's interval in the rule's digits, and its units."""
    for tier in ("live", "owner"):
        cells = r7_docs[tier]["findings"]["c3"]["design_cells"]
        assert [c["key"] for c in cells] == ["width=256", "width=512"], cells
        for c in cells:
            assert re.fullmatch(r"\[[+-]\d[\d.]*, [+-]\d[\d.]*\]", c["interval_text"]) and c["n"] == 3 and c["unit"], c


def test_work_filed_under_no_question_names_its_experiments(r7_docs):
    for tier in ("live", "owner"):
        assert r7_docs[tier]["unfiled"]["experiments"] == ["e4"]


def test_a_call_gives_its_own_command_as_rb_status_does(r7, r7_docs):
    """c2 misses and no call on it is recorded: its call carries rb status's own command for it."""
    item = [i for i in r7.json(Writers.HUMAN, "status")["data"]["open"] if i["subject"] == "c2"][0]
    for tier in ("live", "owner"):
        assert calls_of(r7_docs[tier])["c2"]["do"] == item["do"] and item["do"].startswith("rb decide c2 ")


def test_a_fork_names_the_address_it_was_cloned_from(tmp_path, monkeypatch, capsys):
    """A project cloned from an address says it on rb open, and on a page only where rb export --url names the
    address it will be served from; a path on a machine is never said."""
    as_the_user(monkeypatch)
    for d in "abc":
        (tmp_path / d).mkdir()
    src = r7_record(tmp_path / "src")
    you = Writers.HUMAN
    out = Path(src.json(you, "export", "--with-record", "--out", str(tmp_path / "bundle"))["data"]["path"])
    fork = tmp_path / "fork"
    src.rb(you, "clone", str(out), str(fork))
    # rb clone keeps the path it was given: a path on this machine, never said
    assert both_tiers(fork, tmp_path / "a")["live"]["project"]["parent"] == ""
    project = fork / ".rb" / "project.yaml"
    text = project.read_text(encoding="utf-8")
    project.write_text(re.sub(r"(?m)^  url: .*$", '  url: "https://ana:s3cret@lab.example/int8/bundle.zip?token=s3cret"', text, count=1), encoding="utf-8")
    Writers(fork).rb(you, "doctor", "--adopt", "--why", "where it came from")
    docs = both_tiers(fork, tmp_path / "c")
    assert docs["live"]["project"]["parent"] == "https://lab.example/int8/bundle.zip" and docs["owner"]["project"]["parent"] == ""


def test_the_demo_s_lines_read_plainly(int8_demo, tmp_path):
    """Look 2, items 4 and 12: on the INT8 demo, an investigation says the day it began, never the decision's id, in rb
    status's line and in the document's open items alike; a rule across fixed design cells says how many units fall on
    which side of it, and its timing says its units the same way; what is still open in the record leads with its count."""
    import rbtest
    root = Path(getattr(int8_demo, "root", int8_demo))
    status = subprocess.run([*rbtest.command(), "status"], cwd=root, env=rbtest.environment(), capture_output=True, text=True,
                            stdin=subprocess.DEVNULL).stdout
    doc = contract.model_document(root, "live", tmp_path)
    assert re.search(r"c2 · investigating since \d{4}-\d\d-\d\d · note: ", status), status
    assert not re.search(r"investigating since d[0-9a-z]{6}\b", status + json.dumps(doc)), [l for l in status.splitlines() if "investigating" in l]
    rule = json.dumps(doc["findings"]["c6"])
    assert "1 of 4 units, in 4 design cells, is below -1 (calib_size=256, seed=1)" in status, status
    assert "1 of 4 units, in 4 design cells, was produced before it was declared" in status, status
    assert "outside -1" not in status + rule and "in its 4 design cells" not in status + rule
    # Look 3, also seen: a level with no arm named is said by its metric, never as ": 75.38 %"; DESIGN-PASS-1 P5: in a
    # sentence, with 2 decimals; FIX4: by its title, as the card says it, never its key; FIX6: an acronym in capitals
    # ("ONNX top-1")
    c4 = doc["findings"]["c4"]
    assert c4["row"]["metric"] == "onnx_top1", c4["row"]
    assert c4["reading_short"] == f'ONNX top-1 is {c4["reading"]["estimate"]:.2f}%', c4["reading_short"]
    # FIX4: the row's rule in the card's words, a level's percentage written onto its number
    assert c4["row"]["criterion"] == "≥ 75.5%" and c4["row"]["margin"] == f'margin {c4["reading"]["estimate"] - 75.5:.2f} points', c4["row"]
    assert not any(r["detail"].startswith(": ") or " · : " in r["detail"] for r in doc["home"]["queue"]), doc["home"]["queue"]
    # FIX4: a level's interval in brackets, each bound with its unit, as the list and the card say it ("INT8 is 7.10 ms
    # (6.85 ms to 7.35 ms)"); a difference's bounds in brackets, in the points or units just said
    for cid, f in doc["findings"].items():
        r, said = f["reading"], f["reading_short"]
        if r.get("lo") is None:
            continue
        unit = r["unit_said"]
        cited = r"(; the cited number is [\d.]+%?(?: \S+)?)?$"
        if f["reference_label"] and not f["reference_label"].startswith("cited "):
            assert re.search(r" \([+-]?[\d.]+ to [+-]?[\d.]+\)$", said), (cid, said)
            continue
        # a level, a cited one too: its bounds with its unit in the short sentence and in the card's verdict alike
        bound = {"%": r"[\d.]+%", "": r"[\d.]+"}.get(unit, rf"[\d.]+ {re.escape(unit)}")
        assert re.search(rf" \({bound} to {bound}\){cited}", said), (cid, said)
        assert re.search(rf" \({bound} to {bound}\)", f["verdict"]), (cid, f["verdict"])
    assert any(re.fullmatch(r"INT8 is [\d.]+ ms \([\d.]+ ms to [\d.]+ ms\)", f["reading_short"]) for f in doc["findings"].values()), \
        [f["reading_short"] for f in doc["findings"].values()]
    # a band around a cited percentage: the % on its number and the band in points, as the verdict says it
    c2 = doc["findings"]["c2"]
    assert c2["row"]["criterion"] == "= 76.1% ± 0.05 points", c2["row"]
    assert re.fullmatch(r"FP32 baseline is [\d.]+% \([\d.]+% to [\d.]+%\); the cited number is 76\.1%", c2["reading_short"]), c2["reading_short"]
    # a mean across fixed design cells says so, since one cell misses the rule the mean meets
    assert doc["findings"]["c6"]["reading_short"].startswith("INT8 is 0.4") and " on average across its design cells" in doc["findings"]["c6"]["reading_short"]
    # Look 3 item 1, plain words pass 4: under what needs a judgment, the count says what it counts, never more of what
    # waits, in one wording in every voice
    owner = contract.model_document(root, "owner", tmp_path)
    for line in (doc["home"]["open_line"], owner["home"]["open_line"]):
        assert re.fullmatch(r"Also open: (1 item that needs|\d+ items that need) no decision( · .*)?", line), line
        assert "still open" not in line and "none of them" not in line
    assert doc["home"]["open_line"] == owner["home"]["open_line"]


def test_a_question_nothing_tests_says_no_rule_tests_it(tmp_path):
    """Plain words pass 4, M item 2: a question with nothing linked or filed under it is headed in rule's one name."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you = Writers.HUMAN
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rec.rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    for tier in ("live", "owner"):
        q1 = next(q for q in contract.model_document(root, tier, tmp_path)["questions"] if q["id"] == "q1")
        assert q1["overview"]["headline"] == "No rule tests this question yet", q1["overview"]
        assert "test linked" not in json.dumps(q1)


def test_the_words_given_with_a_comparison_s_declaration_are_live_only(tmp_path):
    """MP-1021-09, contract 1.5: c2 is declared to reverse c1 with the user's words; rb open's document keeps them on
    the declaration, and a page's says the declaration with its reason and no words."""
    rec = r7_record(tmp_path / "int8")
    rec.rb(Writers.HUMAN, "criterion", "relate", "c2", "reverses", "c1", "--orientation", "same", "--why", "the same comparison, read tighter",
           "--words", "c2 should read the other way round from c1")
    (tmp_path / "dumps").mkdir()
    docs = both_tiers(rec.root, tmp_path / "dumps")
    for tier, words in (("live", "c2 should read the other way round from c1"), ("owner", "")):
        [q] = [q for q in docs[tier]["questions"] if q["id"] == "q1"]
        decls = [d for r in q["overview"]["relations"] for d in r["declarations"]]
        assert decls and all(d["why"] == "the same comparison, read tighter" for d in decls), (tier, decls)
        assert [d["words"] for d in decls] == [words] * len(decls), (tier, decls)
        assert ("the other way round" in json.dumps(docs[tier])) is bool(words), tier


# ---------------------------------------------------------------- Overview v2 (contract 1.6)

# what no row of Needs your judgment holds (OVERVIEW-V2.md): a command, a hash, a path
NO_ROW_TEXT = re.compile(r"\brb [a-z]+ |@[0-9a-f]{12}|\b[0-9a-f]{12,}\b|\w/\w")


def ids_in_rows(doc: dict) -> list:
    """Each queue row that names a record by its id (a criterion, an experiment, a question, a call that waits), with
    the id: ids live in the inspector (Look 3 item 2)."""
    ids = set(doc["findings"]) | {e["id"] for e in doc["experiments"]} | {q["id"] for q in doc["questions"]} | {r["id"].split("@")[0] for r in doc["pending"]["rows"]}
    # the author's own words may hold what reads as an id ("int8 against fp32, e3"): those are words, taken out first
    words = sorted({f["statement"] for f in doc["findings"].values()} | {e["title"] for e in doc["experiments"]} | {e["short_title"] for e in doc["experiments"]}
                   | {q["text"] for q in doc["questions"]} | {m["line"] for m in doc["made_calls"]}, key=len, reverse=True)
    rows = []
    for r in doc["home"]["queue"]:
        said = r["title"] + " " + r["detail"]
        for w in filter(None, words):
            said = said.replace(w, " ")
        rows += [(r["title"], r["detail"], i) for i in ids if re.search(rf"(?<![\w.-]){re.escape(i)}(?![\w.-])", said)]
    return rows


def queue_said(doc: dict) -> list:
    return [(r["kind"], r["rank"], r["title"], r["detail"]) for r in doc["home"]["queue"]]


@pytest.mark.parametrize("tier", ["live", "owner"])
def test_the_queue_is_in_the_fixed_order(calls_docs, tier):
    """OVERVIEW-V2.md, the queue's order: the cited fp32 baseline not reproduced (c3) first, labelled as the baseline,
    with its observed and cited numbers; then every other call waiting on the user, one row each; then the session's
    proposal on c2; then the result rb found and nobody recorded. Each row is a verb phrase with one line of numbers or
    names, its target and its button, never a command, hash or path; the ranks never go down."""
    doc = calls_docs[tier]
    queue = doc["home"]["queue"]
    ranks = [r["rank"] for r in queue]
    assert ranks == sorted(ranks), queue_said(doc)
    first = queue[0]
    c3 = doc["findings"]["c3"]
    assert (first["kind"], first["rank"], first["target"], first["action"]) == ("baseline", 1, "c3", "Review"), queue_said(doc)
    assert first["title"] == "Review the FP32 baseline mismatch", first
    est = c3["reading"]["estimate"]
    lo, hi = c3["reading"]["lo"], c3["reading"]["hi"]
    assert first["detail"] == f"FP32 baseline is {est:.2f}% ({lo:.2f}% to {hi:.2f}%); the cited number is 76.13%", first
    calls = [r for r in queue if r["kind"] == "call"]
    assert {r["target"] for r in calls} | {"c3"} == {c["subject"] for c in doc["calls"]}, queue_said(doc)
    assert all(r["rank"] == 2 for r in calls)
    [proposed] = [r for r in queue if r["kind"] == "pending_call"]
    # Look 3 item 2: the thing by its words, never its id, and not said twice
    assert (proposed["rank"], proposed["title"]) == (4, "Confirm the proposed call to accept: INT8 keeps top-1 within a tenth of a point"), proposed
    assert proposed["detail"] == "", proposed
    [found] = [r for r in queue if r["kind"] == "found"]
    [e2] = [e for e in doc["experiments"] if e["id"] == "e2"]
    assert (found["rank"], found["title"], found["action"], found["detail"]) == (5, "Record a result found in the repository", "Inspect", f"for {e2['short_title'] or e2['title']}"), found
    for r in queue:
        assert set(r) == {"kind", "title", "reader_title", "detail", "target", "targets", "action", "rank"}, r
        assert not NO_ROW_TEXT.search(r["title"] + " " + r["detail"]), r
    assert not ids_in_rows(doc), ids_in_rows(doc)


# the verbs a queue row's title opens with when it asks the owner to act
ASKS = {"Review", "Confirm", "Choose", "Revisit", "Record", "Inspect", "Investigate", "Accept", "Reject", "Decide"}


@pytest.mark.parametrize("tier", ["live", "owner"])
def test_a_readers_queue_row_says_what_stands(calls_docs, tier):
    """A reader of a share link is asked to do nothing: each queue row also says what stands, with no verb to act on
    first. The cited fp32 baseline says its number against the cited one, two decimals each; a criterion's row says
    its statement and its rule's state; the proposed call says it is not confirmed; the found result that it is not
    recorded."""
    queue = calls_docs[tier]["home"]["queue"]
    c3 = calls_docs[tier]["findings"]["c3"]
    for r in queue:
        said = r["reader_title"]
        assert said and said.split()[0].strip(":") not in ASKS and said != r["title"], r
        assert not NO_ROW_TEXT.search(said), r
    by = {r["kind"]: r for r in queue}
    assert any(r["reader_title"].startswith("The earlier call on ") for r in queue), queue
    # FIX4: its numbers as the row's detail says them, each with its unit, the cited one in the rule's own digits
    assert by["baseline"]["reader_title"] == f"The FP32 baseline doesn't match its cited number ({c3['reading']['estimate']:.2f}% against 76.13%)", by["baseline"]
    assert f"{c3['reading']['estimate']:.2f}%" in by["baseline"]["detail"] and "76.13%" in by["baseline"]["detail"], by["baseline"]
    assert by["pending_call"]["reader_title"] == "The proposed call to accept is not confirmed: INT8 keeps top-1 within a tenth of a point", by["pending_call"]
    assert by["found"]["reader_title"] == "A result found in the repository is not recorded", by["found"]
    for r in (r for r in queue if r["kind"] == "call" and r["title"].startswith("Review: ")):
        f = calls_docs[tier]["findings"].get(r["target"])
        if f:
            assert r["reader_title"].startswith(f["statement"].rstrip(".") + ": "), r


def test_a_readers_baseline_row_tells_close_numbers_apart(tmp_path):
    """top1 kept as a share: the fp32 baseline at 0.7597 against the cited 0.7613 says both to four digits, never
    "0.76 against 0.76"."""
    rec = Writers(tmp_path / "share")
    rec.root.mkdir(parents=True)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (rec.root / "README.md").write_text("The published fp32 top-1 is 0.7613.\n", encoding="utf-8")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "fp32 top-1 matches the published 0.7613", "-e", "e1", "-q", "q1", "--metric", "fp32.top1", "--equals", "0.7613",
       "--tolerance", "0.00005", "--source", "README.md", "--quote", "The published fp32 top-1 is 0.7613.", "--id", "c1")
    for seed, (fp32, int8) in enumerate([(0.7595, 0.7568), (0.7599, 0.7570), (0.7597, 0.7566)], 1):
        rb(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    queue = dumped(rec.root, "page", tmp_path)[0]["home"]["queue"]
    [row] = [r for r in queue if r["kind"] == "baseline"]
    assert row["reader_title"] == "The FP32 baseline doesn't match its cited number (0.7597 against 0.7613)", row


def test_a_readers_row_for_a_rule_taken_back_says_it_by_its_statement(tmp_path):
    """A session takes back the primary rule c1: the reader's row says the rule by its statement, never its id, and
    says no call is made on it yet; nothing in it is still open as a rule."""
    rec = Writers(tmp_path / "taken")
    rec.root.mkdir(parents=True)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
       "--id", "c1")
    rb(CLAUDE_CODE, "retract", "c1", "--why", "the wrong split")
    queue = dumped(rec.root, "page", tmp_path)[0]["home"]["queue"]
    [row] = [r for r in queue if r["target"] == "c1"]
    assert row["reader_title"] == 'The rule "INT8 keeps top-1 within a point of FP32" was taken back, and no call is made on that yet', row
    assert row["title"] == "Review the rule taken back: INT8 keeps top-1 within a point of FP32", row


def test_a_readers_queue_row_for_designs_and_a_changed_file(int8_demo, tmp_path):
    """On the INT8 demo, the designs' row and the changed file's row say what stands, never an ask."""
    root = Path(getattr(int8_demo, "root", int8_demo))
    queue = contract.model_document(root, "owner", tmp_path)["home"]["queue"]
    by = {r["kind"]: r for r in queue}
    n = len(by["design"]["targets"])
    assert by["design"]["reader_title"] == f"The designs of {n} experiments are not confirmed", by["design"]
    assert by["uncited"]["reader_title"].endswith("changed file nothing in the record cites: export.yaml") or by["uncited"]["reader_title"].endswith(
        "changed files nothing in the record cites"), by["uncited"]
    for r in queue:
        assert r["reader_title"].split()[0] not in ASKS, r


def test_the_queue_holds_each_design_in_one_row_and_each_call_waiting_for_a_confirmation(int8_demo, tmp_path):
    """On the INT8 demo, the designs waiting for a confirmation are one row naming each experiment, ranked after the
    calls on results, and each proposed or past call a row of its own after them; nothing in a row is a command, a
    hash or a path, and a row's target is an object the document holds."""
    root = Path(getattr(int8_demo, "root", int8_demo))
    doc = contract.model_document(root, "live", tmp_path)
    queue = doc["home"]["queue"]
    designs = [r for r in queue if r["kind"] == "design"]
    waiting = [p["id"].split("@")[0] for p in doc["pending"]["rows"] if p["kind"] == "design"]
    assert len(designs) == 1 and designs[0]["targets"] == waiting and designs[0]["rank"] == 3, queue_said(doc)
    assert designs[0]["title"] == f"Confirm {len(waiting)} experiment designs", designs
    pending = [p["id"] for p in doc["pending"]["rows"] if p["kind"] in ("proposed", "past")]
    assert sorted(r["target"] for r in queue if r["kind"] == "pending_call") == sorted(pending), queue_said(doc)
    assert [r["rank"] for r in queue] == sorted(r["rank"] for r in queue)
    held = set(doc["findings"]) | {e["id"] for e in doc["experiments"]} | {p["id"] for p in doc["pending"]["rows"]} | {q["id"] for q in doc["questions"]}
    held |= {f["request_id"] for f in doc["home"]["found"]}
    for r in queue:
        assert not NO_ROW_TEXT.search(r["title"] + " " + r["detail"]), r
        assert r["kind"] == "uncited" or r["target"] in held, r
    [uncited] = [r for r in queue if r["kind"] == "uncited"]
    assert uncited["rank"] == 5 and "export.yaml" in uncited["detail"].split(" · ") and "/" not in uncited["detail"], uncited


def test_the_standing_card_says_why_by_the_queues_order(calls_docs):
    """q1's standing: rb's state word, and one line naming the two strongest reasons in the fixed order, the fp32
    baseline not reproduced first, then the criteria not met counted among those linked to q1; the tallies are only the
    rule words some criterion linked to q1 has, and add up to them. Only a top-level question has a card."""
    doc = calls_docs["live"]
    [card] = doc["home"]["standing"]
    [q1] = [q for q in doc["questions"] if q["id"] == "q1"]
    assert (card["question"], card["state"], card["state_word"]) == ("q1", "open", "Open question"), card
    states = [doc["findings"][c]["state"] for c in q1["findings"]]
    missed = [c for c in q1["findings"] if doc["findings"][c]["state"] in ("misses", "not reproduced") and c != "c3"]
    n = len(q1["findings"])
    assert [r["kind"] for r in card["reasons"]] == ["baseline", "not_met"], card
    assert card["line"] == (f"No answer yet: the fp32 baseline doesn't match its cited number, and {len(missed)} of {n} rules "
                            f"{'is' if len(missed) == 1 else 'are'} not met."), card
    assert card["reasons"][1]["criteria"] == missed
    assert sum(t["n"] for t in card["tallies"]) == n and all(t["n"] for t in card["tallies"])
    assert {t["state"]: t["n"] for t in card["tallies"]} == {s: states.count(s) for s in set(states)}
    assert card["tallies"] == q1["tallies"] and q1["compact"] == " · ".join(["open"] + [t["label"] for t in q1["tallies"]])


def test_the_arms_are_labelled_wherever_a_reading_names_them(calls_docs):
    """A baseline is labelled as one wherever it appears: c3 reads the fp32 baseline alone, against its cited number;
    c1 reads int8 against the fp32 baseline; e1 compares int8 against the fp32 baseline."""
    f = calls_docs["owner"]["findings"]
    assert (f["c3"]["subject_label"], f["c3"]["reference_label"], f["c3"]["reads_baseline"]) == ("fp32 baseline", "cited 76.13 %", True)
    assert f["c3"]["reading_short"].startswith("FP32 baseline is ") and f["c3"]["state"] == "not reproduced"
    assert (f["c1"]["subject_label"], f["c1"]["reference_label"], f["c1"]["reads_baseline"]) == ("int8", "fp32 baseline", False)
    assert re.match(r"INT8 is \d+\.\d\d points (lower|higher) than the FP32 baseline", f["c1"]["reading_short"]) and f["c1"]["state_label"] == "met"
    [e1] = [e for e in calls_docs["owner"]["experiments"] if e["id"] == "e1"]
    # FIX4: each variant by its plain name, as the grid and the runs table say it
    assert e1["compares"] == "INT8 against the FP32 baseline"
    assert f["c14"]["reading_short"] == "no reading yet" and f["c14"]["tags"][0] == "criterion_untested"
    assert "retrospective" in f["c12"]["tags"] and len(f["c12"]["tags"]) <= 2


def test_integrity_counts_what_weakens_the_whole_body_of_evidence(int8_demo, tmp_path):
    """On the INT8 demo: the criteria declared after their evidence and the runs without their command as given, as
    home.made counts them, the retracted run, each a plain line with its numbers and its ids; then the owner's line on
    what they affect."""
    root = Path(getattr(int8_demo, "root", int8_demo))
    doc = contract.model_document(root, "owner", tmp_path)
    made, integrity = doc["home"]["made"], doc["home"]["integrity"]
    parts = {p["kind"]: p for p in integrity["parts"]}
    late = parts["after_evidence"]
    assert (late["n"], late["of"]) == (made["after_evidence"], made["criteria"]) and len(late["ids"]) == late["n"], late
    one = late["n"] == 1
    assert late["line"] == f"{late['n']} of {late['of']} rules {'was' if one else 'were'} written after {'its' if one else 'their'} results came in.", late
    assert set(late["ids"]) <= set(doc["findings"])
    none = parts["without_command"]
    assert (none["n"], none["of"]) == (made["runs"] - made["with_command"], made["runs"]), none
    assert none["line"] == f"{none['n']} of {none['of']} runs have no command from when they ran.", none
    retracted = [r for r, v in doc["runs"].items() if v["retracted"]]
    assert parts["retracted"]["ids"] == retracted and parts["retracted"]["line"] == f"{len(retracted)} run{' was' if len(retracted) == 1 else 's were'} retracted."
    assert integrity["affects"] == "Treat them as a record of what happened, not as a test planned in advance."


def test_nothing_weakens_a_record_with_no_evidence(tmp_path):
    """A record of one question and nothing else: no part of integrity and no line; the queue is empty; the standing
    card says no criterion is linked to the question."""
    rec = Writers(tmp_path / "int8")
    (tmp_path / "int8").mkdir()
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rec.rb(Writers.HUMAN, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb(Writers.HUMAN, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    doc = contract.model_document(rec.root, "live", tmp_path)
    assert doc["home"]["integrity"] == {"parts": [], "affects": ""}
    assert doc["home"]["queue"] == []
    [card] = doc["home"]["standing"]
    assert card["line"] == "No rule tests this question yet." and card["tallies"] == [], card
    assert card["interpretation"]["line"] == ""


def test_a_recorded_interpretation_is_one_line_on_its_card(int8_demo, tmp_path):
    """The INT8 demo's q1 holds the user's interpretation of c1, active while c1 meets its rule: its card says it in one
    line, its words then its criteria's state, as the question's lead and overview say them; q1a and q1b, below it, have
    no card."""
    root = Path(getattr(int8_demo, "root", int8_demo))
    doc = contract.model_document(root, "live", tmp_path)
    [card] = doc["home"]["standing"]
    [q1] = [q for q in doc["questions"] if q["id"] == "q1"]
    lead = q1["lead"]
    assert card["question"] == "q1" and lead["kind"] == "interpretation" and lead["active"], lead
    holds = q1["overview"]["interpretations"][lead["index"]]["holds"]
    # PLAIN-WORDS-PASS-3: a note with its state in the line, never a reading or rb's verdict; its rule's state a
    # sentence of its own
    label = "Note" if lead["confirmed"] else "A note, not confirmed"
    stop = "" if lead["text"].endswith(".") else "."
    assert holds == "Its rule is met.", holds
    assert card["interpretation"] == {"line": f"{label}: {lead['text']}{stop} {holds}", "text": lead["text"], "holds": holds,
                                      "confirmed": lead["confirmed"]}, card
    assert card["state_word"] == "Open question", card


def test_what_is_also_open_reads_as_a_plain_checklist(int8_demo, tmp_path):
    """DESIGN-PASS-1 target 2: on the INT8 demo, each group of what is still open in the record is headed by a plain
    sentence in the screens' words, each item one short sentence that never leads with its id (the id is its subject,
    which a screen shows after it), one item per rule; a group's count is its lines, and the Overview's line and the
    quiet line count the same lines."""
    root = Path(getattr(int8_demo, "root", int8_demo))
    for surface in ("live", "owner"):
        (tmp_path / surface).mkdir()
        doc = contract.model_document(root, surface, tmp_path / surface)
        groups, unfiled = doc["open_items"], doc["unfiled"]["open_items"]
        heads = {g["code"]: g["head"] for g in groups}
        assert heads["late"] == "Rules written after their results or data were seen" and heads["unindexed_folder"] == "Results not yet in any experiment", heads
        [late] = [g for g in groups if g["code"] == "late"]
        assert [(i["subject"], i["what"]) for i in late["items"]] == [("c6", f'{doc["findings"]["c6"]["statement"]}: written after 1 of its 4 results came in')], late
        for g in groups:
            assert g["n"] == len(g["items"]), g
        total = sum(g["n"] for g in groups) + len(unfiled)
        assert doc["home"]["open_line"].startswith(f"Also open: {total} items that need no decision"), doc["home"]["open_line"]
        for i in [i for g in groups for i in g["items"]] + unfiled:
            assert not i["what"].startswith(i["subject"]) and "criteri" not in i["what"], i
            assert i["units"] == "", i
        assert {i["subject"]: i["what"] for i in unfiled}["c5"] == "INT8 answers in 7 ms or less: linked to no question, so no question's answer reads it", unfiled


def test_calls_of_one_rank_go_newest_first(calls_docs):
    """Ties in the queue go by recency: the calls on results, all of rank 2, newest first by the latest of when each
    criterion was declared and when the runs it reads were recorded, as the document says them; the record's calls
    are not all of one minute, so the order says something."""
    doc = calls_docs["live"]

    def latest(subject):
        f = doc["findings"].get(subject)
        if f is None:
            es = [e for e in doc["experiments"] if e["id"] == subject]
            return max([doc["runs"][r]["at"] for e in es for r in e["run_ids"]], default="")
        declared = [row[1] for row in f["receipt_rows"] if row[0] == "declared"]
        return max(declared + [doc["runs"][o["run"]]["at"] for o in f["observations"]])

    calls = [r["target"] for r in doc["home"]["queue"] if r["rank"] == 2]
    assert calls == sorted(calls, key=latest, reverse=True), [(c, latest(c)) for c in calls]
    assert len({latest(c) for c in calls}) > 1, [(c, latest(c)) for c in calls]


def test_a_call_waiting_on_a_sub_question_is_on_its_top_questions_card(tmp_path):
    """A session proposes to park q1a, under q1: the call waits within q1's tree, so q1's card names it among its
    reasons, and the queue holds it."""
    rec = Writers(tmp_path / "int8")
    (tmp_path / "int8").mkdir()
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rec.rb(Writers.HUMAN, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb(Writers.HUMAN, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rec.rb(Writers.HUMAN, "question", "add", "How many calibration images does INT8 need?", "--parent", "q1", "--id", "q1a")
    rec.rb(CLAUDE_CODE, "decide", "q1a", "park", "--why", "the calibration set is not fixed yet")
    doc = contract.model_document(rec.root, "live", tmp_path)
    [card] = doc["home"]["standing"]
    assert card["question"] == "q1" and [r["kind"] for r in card["reasons"]] == ["pending_call"], card
    assert card["line"] == "No rule tests this question yet. 1 call waits for your confirmation.", card
    assert [r["title"] for r in doc["home"]["queue"]] == ["Confirm the proposed call to park: How many calibration images does INT8 need?"], doc["home"]["queue"]


def test_a_missed_gates_row_names_the_gates_in_one_line(gates_docs):
    """e1's three missed gates are one call, so one queue row: "Choose whether int8 against fp32 continues", its detail
    the gates by their statements, never their ids (Look 3 item 2) or whole readings; the same on both tiers."""
    for tier in ("live", "owner"):
        doc = gates_docs[tier]
        [row] = [r for r in doc["home"]["queue"] if r["target"] == "e1"]
        assert (row["kind"], row["rank"], row["title"]) == ("call", 2, "Choose whether int8 against fp32 continues"), row
        said = [doc["findings"][g]["statement"] for g in ("g1", "g2", "g3")]
        assert row["detail"] == f"3 gates missed: {said[0]} · {said[1]} and 1 more", row
        assert not ids_in_rows(doc), ids_in_rows(doc)


def test_reproduce_asks_for_the_run_rb_status_suggests_and_an_export_says_no_ones_voice(tmp_path):
    """MP-1022-03, P's line 8 and C's line 33, on the INT8 record with c9, INT8's latency, untested: e1's Reproduce
    request asks for the run rb status suggests first (a latency_ms measurement), not one more top1; and an export,
    which anyone may be sent, says its quiet line in no one's voice, where rb page keeps the user's."""
    rec = int8_record(tmp_path / "int8")
    rec.rb(Writers.HUMAN, "metric", "add", "latency_ms", "--minimize", "--unit", "ms")
    rec.rb(Writers.HUMAN, "criterion", "add", "INT8 adds at most 2 ms", "-e", "e1", "-q", "q1", "--metric", "change.latency_ms", "--at-most", "2", "--id", "c9")
    page = contract.model_document(rec.root, "owner", tmp_path)
    [e1] = page["experiments"]
    assert "latency_ms" in e1["reproduce"]["prompt"] and "top1" not in e1["reproduce"]["prompt"], e1["reproduce"]["prompt"]
    dump = tmp_path / "export.json"
    _, said = contract.drawn(rec.root, "export", dump=dump)
    export = json.loads(dump.read_text(encoding="utf-8"))
    assert page["home"]["quiet"] == "1 call waits for your confirmation, listed apart; nothing else waits on you.", page["home"]["quiet"]
    assert export["home"]["quiet"].startswith("1 call waits for a confirmation, listed apart."), export["home"]["quiet"]
    assert not re.search(r"\byou(r)?\b", export["home"]["quiet"]), export["home"]["quiet"]


def test_an_export_says_no_one_s_confirmation_on_calls(tmp_path):
    """Follow-up of Oct 22 (R's line 72): rb export's document, which anyone may be sent, says what waits for a
    confirmation in no one's voice: each question's standing line, the waiting line and every other string on calls or
    confirmations says neither your nor you; rb page, the user's own, keeps "your confirmation"."""
    rec = int8_record(tmp_path / "int8")
    page = contract.model_document(rec.root, "owner", tmp_path)
    assert any("your confirmation" in s["line"] for s in page["home"]["standing"]), page["home"]["standing"]
    dump = tmp_path / "export.json"
    contract.drawn(rec.root, "export", dump=dump)
    export = json.loads(dump.read_text(encoding="utf-8"))
    lines = [s["line"] for s in export["home"]["standing"]]
    assert any("waits for a confirmation" in line or "wait for a confirmation" in line for line in lines), lines
    assert export["home"]["waiting_line"].endswith("for a confirmation, listed apart"), export["home"]["waiting_line"]

    def strings(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from strings(x)
        elif isinstance(v, list):
            for x in v:
                yield from strings(x)

    said = [s for s in strings(export) if re.search(r"\b(call|calls|confirm\w*)\b", s, re.I) and re.search(r"\byou(r|rs)?\b", s, re.I)]
    assert not said, said


def test_a_past_version_offers_no_rb_confirm_command(tmp_path, monkeypatch):
    """Guarantee 38: a past version offers no command to copy. Where a session's design, its gate and a proposed call
    wait, rb open's document at a past version names no rb confirm anywhere: the design's own line says it is not
    confirmed, and the line for the designs that wait says how many, each with no command; live, both still name it."""
    as_the_user(monkeypatch)
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you = Writers.HUMAN
    rec.git("init", "-q", "-b", "main")
    rec.rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb(you, "question", "add", "Does INT8 cost accuracy on ImageNet?", "--id", "q1")
    rec.rb(CLAUDE_CODE, "experiment", "add", "INT8 against FP32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8",
           "--if-holds", "INT8 ships")
    rec.rb(CLAUDE_CODE, "decide", "q1", "investigate", "--why", "one more seed")
    # one criterion linked to q1, one filed under it with no link: its listing offers the filing that links it
    rule = ["-e", "e1", "--metric", "change.top1", "--at-least", "-1"]
    rec.rb(you, "criterion", "add", "INT8 keeps top-1 within a point", *rule, "-q", "q1", "--id", "c1")
    rec.rb(you, "criterion", "add", "INT8 keeps top-1 within a point, not linked", *rule, "--id", "c2")
    rec.git("add", "-A")
    rec.git("-c", "user.name=ana", "-c", "user.email=ana@example.org", "commit", "-q", "-m", "the record")
    rec.rb(you, "note", "add", "the held-out set is ImageNet validation")
    rec.git("add", "-A")
    rec.git("-c", "user.name=ana", "-c", "user.email=ana@example.org", "commit", "-q", "-m", "a note")
    dump = tmp_path / "doc.json"
    monkeypatch.setenv("RB_TEST_SHELL", "1")
    monkeypatch.setenv("RB_TEST_SHELL_DUMP", str(dump))
    server = Open(root)
    try:
        def doc(query: str = "") -> dict:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(server.url + "/" + query, timeout=60) as r:
                assert r.status == 200
            return json.loads(dump.read_text(encoding="utf-8"))
        live = doc()
        past = doc(f"?at={live['places']['versions'][-1]['sha']}")
    finally:
        server.close()
    assert past["places"]["place"]["read_only"] is True
    # the Where picker names a single checkout by its branch at a past version as live: never by its whole path
    assert [p["label"] for p in past["places"]["places"]] == [p["label"] for p in live["places"]["places"]] == [live["places"]["place"]["line"]]
    assert "/" not in live["places"]["place"]["line"] and past["places"]["place"]["path"] == live["places"]["place"]["path"]
    [e1] = [e for e in live["experiments"] if e["id"] == "e1"]
    assert "rb confirm e1" in json.dumps(e1["design"]) and "rb confirm lists it" in live["pending"]["designs_line"]
    assert "rb confirm" in json.dumps(live["pending"]["rows"]) and "Link it here: rb criterion file c2 q1" in json.dumps(live["questions"])
    [e1] = [e for e in past["experiments"] if e["id"] == "e1"]
    assert "design not confirmed" in e1["design"]["lines"], e1["design"]
    assert past["pending"]["designs_line"] == "The design of 1 experiment is not confirmed.", past["pending"]
    assert [r["kind"] for r in past["pending"]["rows"]] == [r["kind"] for r in live["pending"]["rows"]]
    said = json.dumps(past, ensure_ascii=False)
    assert "rb criterion file" not in said and "unassigned (filed here, no question link): c2: " in said
    assert "rb confirm" not in said, [m.start() for m in re.finditer("rb confirm", said)][:3] and said[said.find("rb confirm") - 200:said.find("rb confirm") + 80]


def test_the_integrity_strip_says_runs_with_a_later_command_have_none_from_when_they_ran(tmp_path):
    """D's request at MP-1023-03: runs that each record a command given later, not observed, are said as having no
    command from when they ran, never as recording none: what rb status says of them holds on the strip too."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you = Writers.HUMAN
    rec.git("init", "-q")
    rec.rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rec.rb(you, "experiment", "add", "INT8 against FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rec.rb(you, "spec", "set", "e1", "seed", "--per-run")
    for seed, top1 in ((1, 75.9), (2, 76.0)):
        rec.rb(you, "run", "add", "e1", "fp32.top1=76.1", f"int8.top1={top1}", "--set", f"seed={seed}", "--command", f"python eval.py --seed {seed}",
               "--produced-at", "2026-05-01")
    runs = json.loads(rec.run(you, "status", "--json").stdout)["data"]["runs"]
    assert [r["command"]["given"] for r in runs] == ["later", "later"], runs
    doc = contract.model_document(root, "owner", tmp_path)
    [none] = [p for p in doc["home"]["integrity"]["parts"] if p["kind"] == "without_command"]
    assert none["line"] == "None of the 2 runs has a command from when it ran.", none
    assert "record" not in none["line"]


def cited_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet whose notes and calls cite files by line: README.md and notes.md committed, a log git
    tracks (logs/eval.log), a session's transcript git tracks (agent_session.md), a scratch file git does not track, and
    a line README.md does not have."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    (root / "README.md").write_text("# INT8 ResNet-50 on ImageNet\n\nDoes INT8 cost top-1 on ImageNet?\n\nWe ship INT8 if it loses at most a point.\n"
                                    "\nResNet-50 v1.5 reaches 76.13% top-1.\n", encoding="utf-8")
    (root / "notes.md").write_text("# Calls\n\n2026-05-07: We park INT8 until the calibration set is fixed.\n", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "eval.log").write_text("epoch 1\nint8 top1 75.53\nfp32 top1 75.69\n", encoding="utf-8")
    (root / "agent_session.md").write_text("ran eval.py\nint8 75.53\n", encoding="utf-8")
    rec.git("add", "-A")
    rec.git("commit", "-q", "-m", "the study")
    (root / "scratch.md").write_text("calibration draws\nseed 1 drew 512 images\n", encoding="utf-8")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "note", "add", "see README.md:3 and README.md:5-7", "-e", "e1")
    rb(you, "note", "add", "the numbers are in logs/eval.log:2, the session in agent_session.md:2", "-e", "e1")
    rb(you, "note", "add", "the draw is in scratch.md:2; README.md:40 is gone")
    rb(CLAUDE_CODE, "decide", "e1", "park", "--why", "notes.md:3", "--past", "--when", "2026-05-07", "--source", "notes.md:3", "--quote",
       "We park INT8 until the calibration set is fixed")
    return rec


@pytest.fixture(scope="module")
def cited(tmp_path_factory):
    rec = cited_record(tmp_path_factory.mktemp("cited") / "int8")
    out = {}
    for surface in ("page", "open", "export"):
        dump = rec.root.parent / f"{surface}.json"
        _, said = contract.drawn(rec.root, surface, dump=dump)
        assert dump.exists(), said
        out[surface] = json.loads(dump.read_text(encoding="utf-8"))
    return rec, out


def test_a_citation_opens_the_lines_it_cites(cited):
    """CITE item 1: each file:line a note, a source or a call names is resolved to its lines, one line of context each
    side, the commit it was read at and the file's sha256, with the objects that cite it; a range keeps every line."""
    rec, docs = cited
    head = rec.git("rev-parse", "HEAD")
    readme = __import__("hashlib").sha256((rec.root / "README.md").read_bytes()).hexdigest()
    for surface, doc in docs.items():
        c = doc["citations"]
        three = c["README.md:3"]
        assert (three["path"], three["name"], three["folder"], three["line"], three["end"]) == ("README.md", "README.md", "", 3, 3)
        assert three["label"] == "README.md · line 3" and three["raw"] == ["README.md:3"]
        assert (three["commit"], three["commit_short"], three["read"]) == (head, head[:7], f"read at commit {head[:7]}"), three
        assert three["sha256"] == readme and three["tracked"] and three["quoted"] and not three["missing"] and not three["not_quoted"]
        assert three["lines"] == [{"n": 2, "text": "", "cited": False}, {"n": 3, "text": "Does INT8 cost top-1 on ImageNet?", "cited": True},
                                  {"n": 4, "text": "", "cited": False}], three["lines"]
        e1 = next(e for e in doc["experiments"] if e["id"] == "e1")
        assert {"kind": "note", "id": e1["note_ids"][e1["notes"].index(next(n for n in e1["notes"] if "README.md:3" in n))]} in three["cited_by"]
        rng = c["README.md:5-7"]
        assert rng["label"] == "README.md · lines 5-7" and [(x["n"], x["cited"]) for x in rng["lines"]] == [(4, False), (5, True), (6, True), (7, True)]
        gone = c["README.md:40"]
        assert gone["missing"] == f"README.md has no line 40 at commit {head[:7]}" and not gone["quoted"] and gone["lines"] == []
        assert [b["kind"] for b in gone["cited_by"]] == ["note"]
        # a past call cites the document it is given from
        calls = c["notes.md:3"]
        assert calls["lines"][1] == {"n": 3, "text": "2026-05-07: We park INT8 until the calibration set is fixed.", "cited": True}
        assert any(b["kind"] == "call" for b in calls["cited_by"]), calls["cited_by"]
        # rb open alone links the file on this machine
        assert bool(three["open_url"]) == (surface == "open")
        if surface == "open":
            assert three["open_url"].startswith("vscode://file/") and three["open_url"].endswith("/README.md:3"), three["open_url"]


def test_a_shared_page_quotes_only_what_git_tracks_and_no_log_or_transcript(cited):
    """CITE item 4: the export quotes a cited line only from a file git tracks at the commit, never a log or a session's
    transcript, and says so; rb page and rb open quote everything local."""
    rec, docs = cited
    for key in ("logs/eval.log:2", "agent_session.md:2", "scratch.md:2"):
        shared = docs["export"]["citations"][key]
        assert shared["not_quoted"] == "Not quoted on a shared page" and not shared["quoted"], shared
        assert shared["lines"] == [] and shared["sha256"] == "" and shared["read"] == "", shared
        for local in ("page", "open"):
            here = docs[local]["citations"][key]
            assert here["quoted"] and not here["not_quoted"] and any(x["cited"] for x in here["lines"]), here
    assert docs["page"]["citations"]["logs/eval.log:2"]["lines"][1]["text"] == "int8 top1 75.53"
    scratch = docs["page"]["citations"]["scratch.md:2"]
    assert not scratch["tracked"] and scratch["read"] == "read from the file as it is now; git does not track it", scratch
    assert docs["export"]["citations"]["README.md:3"]["quoted"]
    # nothing withheld reaches the shared page's document anywhere
    blob = json.dumps(docs["export"]["citations"])
    assert "seed 1 drew 512 images" not in blob and "int8 top1 75.53" not in blob and "int8 75.53" not in blob


def test_a_shared_page_quotes_the_commit_not_the_working_copy(tmp_path):
    """CITE item 4: a tracked file changed since the commit is quoted on the export as the commit holds it, and on rb
    page as it is now, which says it differs from the commit."""
    rec = cited_record(tmp_path / "int8")
    (rec.root / "README.md").write_text("# INT8 ResNet-50 on ImageNet\n\nDoes INT8 cost top-1 on ImageNet, uncommitted?\n", encoding="utf-8")
    head = rec.git("rev-parse", "--short=7", "HEAD")
    docs = {}
    for surface in ("page", "export"):
        dump = tmp_path / f"{surface}.json"
        contract.drawn(rec.root, surface, dump=dump)
        docs[surface] = json.loads(dump.read_text(encoding="utf-8"))["citations"]["README.md:3"]
    assert docs["export"]["lines"][1]["text"] == "Does INT8 cost top-1 on ImageNet?" and docs["export"]["read"] == f"read at commit {head}"
    assert docs["page"]["lines"][1]["text"] == "Does INT8 cost top-1 on ImageNet, uncommitted?"
    assert docs["page"]["read"] == f"read from the file as it is now; it differs from commit {head}", docs["page"]


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes and links as git keeps them")
def test_a_citation_says_truly_what_it_could_not_read(tmp_path):
    """CITE review: a pipe is never read (rb page would wait on it), a file git holds and the working copy lost is read
    as the commit holds it, an empty file has no line 1, a link is never followed on a shared page, and a host's port
    or a number with a unit is no citation."""
    import os
    root = tmp_path / "int8"
    rec = cited_record(root)
    (root / "gone.md").write_text("first\nsecond\n", encoding="utf-8")
    (root / "empty.md").write_text("", encoding="utf-8")
    os.symlink("logs/eval.log", root / "summary.md")
    rec.git("add", "-A")
    rec.git("commit", "-q", "-m", "more")
    (root / "gone.md").unlink()
    os.mkfifo(root / "pipe.md")
    rec.rb(Writers.HUMAN, "note", "add", "pipe.md:1, gone.md:2, empty.md:1, summary.md:2; example.com:8787 took 12.5s:3")
    head = rec.git("rev-parse", "--short=7", "HEAD")
    docs = {}
    for surface in ("page", "export"):
        dump = tmp_path / f"{surface}.json"
        started = time.monotonic()
        _, said = contract.drawn(root, surface, dump=dump)
        assert dump.exists() and time.monotonic() - started < 60, said
        docs[surface] = json.loads(dump.read_text(encoding="utf-8"))["citations"]
    page, export = docs["page"], docs["export"]
    assert not {"example.com:8787", "12.5s:3"} & set(page), list(page)
    assert page["pipe.md:1"]["missing"] == "pipe.md is not a file" and not page["pipe.md:1"]["quoted"]
    gone = page["gone.md:2"]
    assert gone["read"] == f"read at commit {head}; the file is gone from the working copy" and gone["lines"][1] == {"n": 2, "text": "second", "cited": True}
    assert page["empty.md:1"]["missing"] == f"empty.md has no line 1 at commit {head}" and not page["empty.md:1"]["quoted"]
    link = export["summary.md:2"]
    assert link["not_quoted"] == "Not quoted on a shared page" and link["lines"] == [] and "int8 top1" not in json.dumps(export)


def prompts_record(root: Path) -> Writers:
    """A difference of percentages (PLAIN-WORDS-PASS-4), in the INT8 example's terms: ImageNet top-1 in percent, int8
    against the fp32_ref baseline (an arm named with an underscore), three seeds; c1 asks int8 for at least 5 points
    more, which it misses with a real gain, and c2 asks fp32_ref's own level for at least 70%."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "percent")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32_ref", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 scores at least 5 points above FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "5", "--id", "c1")
    rb(you, "criterion", "add", "FP32 scores at least 70%", "-e", "e1", "-q", "q1", "--metric", "fp32_ref.top1", "--at-least", "70", "--id", "c2")
    for seed, (fp32, int8) in enumerate([(75.0, 78.4), (75.2, 79.0), (75.0, 78.6)], 1):
        rb(you, "run", "add", "e1", f"fp32_ref.top1={fp32}", f"int8.top1={int8}", "--set", f"seed={seed}")
    return rec


@pytest.fixture(scope="module")
def prompts_docs(tmp_path_factory):
    rec = prompts_record(tmp_path_factory.mktemp("shell-model") / "prompts")
    return both_tiers(rec.root, tmp_path_factory.mktemp("prompts-dumps"))


def test_a_rule_says_its_verdict_in_one_plain_sentence(prompts_docs):
    """PLAIN-WORDS-PASS-4: each rule's verdict is one sentence, its status word first, then the reading in points for
    a difference of percentages and the variant's name in words, rb's result class in words, and how far it is from
    the rule's number; the sentence a row says reads in points too, and the unit as a sentence says it is beside
    rb's own."""
    for tier in ("owner", "live"):
        f = prompts_docs[tier]["findings"]
        assert f["c1"]["verdict"] == ("Not met: INT8 is 3.60 points above fp32 ref (+3.10 to +4.10). "
                                      "A real gain, but 1.40 points short of the 5 points the rule asks for."), f["c1"]["verdict"]
        # FIX4: the interval in brackets, as the verdict says it; the row's rule and margin in the card's words and digits
        assert f["c1"]["reading_short"] == "INT8 is 3.60 points higher than the fp32 ref baseline (+3.10 to +4.10)"
        assert (f["c1"]["row"]["criterion"], f["c1"]["row"]["margin"]) == ("≥ 5 points", "margin -1.40 points"), f["c1"]["row"]
        # FIX4: what e1 compares names each variant by its plain name, underscores as spaces and a format in capitals
        [e1] = [e for e in prompts_docs[tier]["experiments"] if e["id"] == "e1"]
        assert e1["compares"] == "INT8 against the fp32 ref baseline", e1["compares"]
        # c2 reads a level: its rule's percentage written onto its number, its margin in points as the verdict says it
        assert f["c2"]["row"]["criterion"] == "≥ 70%" and f["c2"]["row"]["margin"].endswith(" points"), f["c2"]["row"]
        assert (f["c1"]["reading"]["unit_text"], f["c1"]["reading"]["unit_said"]) == ("percent", "points")
        assert f["c2"]["verdict"].startswith("Met: fp32 ref is 75.07% (") and f["c2"]["verdict"].endswith(
            "One arm measured on its own. It is 5.07 points past the 70% the rule asks for."), f["c2"]["verdict"]
        assert f["c2"]["reading"]["unit_said"] == "%"


STATUS = ("Met", "Met on its estimate", "Met on the recorded values", "Not met", "Reproduced", "Not reproduced", "Can't tell yet",
          "Not tested", "Gate met", "Gate missed", "Gate, can't tell yet", "Gate, not tested")


def test_no_verdict_leads_with_a_result_class(prompts_docs, m3_docs, calls_docs, docs):
    """PLAIN-WORDS-PASS-4: a verdict's status is a rule's word, never rb's result class (gain, loss, measured), which
    the sentence says in words after the reading; it is said once, in one field."""
    seen = 0
    for doc in (prompts_docs["owner"], m3_docs["owner"], calls_docs["owner"], docs["owner"]):
        for f in doc["findings"].values():
            v = f["verdict"]
            status, rest = v.split(": ", 1)
            assert status in STATUS, v
            assert v.count(status + ":") == 1 and "—" not in v, v
            assert not re.search(r"(^|[ ·])(gain|loss|measured|unresolved|point only)( ·|$)", v), v
            seen += 1
    assert seen > 15


def baseline_first_record(root: Path, subject_runs_before: int) -> Writers:
    """INT8 ResNet-50 on ImageNet, by the user (human:ana): e1's fp32 baseline run on three seeds, int8 run on
    `subject_runs_before` of them, then rule c1 (int8 against the fp32 baseline), then int8 on the rest; each write a
    minute after the last on the user's clock."""
    root.mkdir(parents=True)
    rec = Writers(root)
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    clock = iter(range(1, 60))
    rb = lambda *argv: rec.rb({**Writers.HUMAN, **at(f"09:{next(clock):02d}")}, *argv)
    rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb("question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb("metric", "add", "top1", "--maximize", "--unit", "%")
    rb("experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    fp32, int8 = [75.69, 75.71, 75.66], [75.53, 75.58, 75.48]
    for seed, v in enumerate(fp32, 1):
        rb("run", "add", "e1", f"top1={v}", "--variant", "fp32", "--set", f"seed={seed}")
    for seed in range(1, subject_runs_before + 1):
        rb("run", "add", "e1", f"top1={int8[seed - 1]}", "--variant", "int8", "--set", f"seed={seed}")
    rb("criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "top1", "--subject", "int8",
       "--reference", "fp32", "--at-least", "-1", "--id", "c1")
    for seed in range(subject_runs_before + 1, 4):
        rb("run", "add", "e1", f"top1={int8[seed - 1]}", "--variant", "int8", "--set", f"seed={seed}")
    return rec


@pytest.mark.parametrize("before", [0, 1])
def test_a_rule_s_timing_is_read_on_its_subject_s_runs(tmp_path, before):
    """A rule written down after its baseline's runs and before any run of its subject reads as written before its
    runs, said so with its baseline's runs coming first, and the trust box counts no rule written after its results;
    with one subject run before it, it reads as written after its results came in, as before."""
    rec = baseline_first_record(tmp_path / "int8", before)
    f = rec.json(Writers.HUMAN, "show", "c1")["data"]["finding"]
    doc = contract.model_document(rec.root, "owner", tmp_path)
    parts = {p["kind"]: p for p in doc["home"]["integrity"]["parts"]}
    status = rec.rb(Writers.HUMAN, "status")
    if before == 0:
        assert f["timing"] == "before", f["says"]
        assert f["says"]["timing"] == "declared before its runs (by record times; its baseline's runs came first)", f["says"]
        assert "after_evidence" not in parts and doc["home"]["made"]["after_evidence"] == 0, parts
        assert "declared before its runs" in status and "its baseline's runs came first" in status, status
        assert "late" not in [i["code"] for i in rec.json(Writers.HUMAN, "status")["data"]["open"]]
        context = rec.rb(Writers.HUMAN, "context")
        assert "declared before its runs (by record times; its baseline's runs came first)" in context, context
        assert "before its runs, when only its baseline's runs came first: timing is read on the subject's runs" in context, context
    else:
        assert f["timing"] == "after_evidence", f["says"]
        assert f["says"]["timing"].startswith("declared after the evidence existed: 1 of 3 units was produced before it was declared; produced after it: "), f["says"]
        assert parts["after_evidence"]["line"] == "The one rule was written after its results came in.", parts


def test_a_rule_before_every_run_reads_before_with_no_baseline_words(tmp_path):
    """A rule written before any run of either arm reads as written before the evidence, with no word of a baseline."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    clock = iter(range(1, 60))
    rb = lambda *argv: rec.rb({**Writers.HUMAN, **at(f"09:{next(clock):02d}")}, *argv)
    rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb("metric", "add", "top1", "--maximize", "--unit", "%")
    rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    rb("criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
       "--at-least", "-1", "--id", "c1")
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        rb("run", "add", "e1", f"top1={fp32}", "--variant", "fp32", "--set", f"seed={seed}")
        rb("run", "add", "e1", f"top1={int8}", "--variant", "int8", "--set", f"seed={seed}")
    f = rec.json(Writers.HUMAN, "show", "c1")["data"]["finding"]
    assert f["timing"] == "before" and f["says"]["timing"] == "declared before the evidence (by record times)", f["says"]


def test_an_analysis_or_a_retraction_reads_the_timing_on_the_subject_s_runs_too(tmp_path):
    """The baseline-first record: an analysis that re-reads its six runs leaves it written before its runs; with one
    int8 run before the rule, taking that run back says the rule is now declared before its runs; with every int8 run
    before it, rb status's late item asks for its subject's runs after the declaration, not both arms."""
    rec = baseline_first_record(tmp_path / "first", 0)
    runs = [r["id"] for r in rec.json(Writers.HUMAN, "status")["data"]["runs"]]
    rec.rb({**Writers.HUMAN, **at("10:00")}, "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.16[-0.22,-0.09]", "--method",
           "paired bootstrap over images", "--reads", ",".join(runs))
    f = rec.json(Writers.HUMAN, "show", "c1")["data"]["finding"]
    assert f["timing"] == "before" and f["says"]["timing"].endswith("its baseline's runs came first)"), f["says"]

    rec = baseline_first_record(tmp_path / "all", 3)
    rec.rb({**Writers.HUMAN, **at("10:00")}, "experiment", "gate", "e1", "--if-holds", "ship INT8")
    [late] = [i for i in rec.json(Writers.HUMAN, "status")["data"]["open"] if i["code"] == "late"]
    assert late["what"] == "e1: 1 criterion was declared after its evidence existed: a reading of its own needs its subject's runs made after the declaration", late
    assert late["do"].endswith("   (run after the declaration)"), late

    rec = baseline_first_record(tmp_path / "one", 1)
    f = rec.json(Writers.HUMAN, "show", "c1")["data"]["finding"]
    [early] = [o["run"] for o in f["observations"] if o["arm"] == "int8" and o["before_declared"]]
    said = rec.rb({**Writers.HUMAN, **at("10:01")}, "retract", early, "--why", "the wrong checkpoint")
    assert " · declared after the evidence existed" in said and " · declared before its runs" in said, said


def test_a_rule_whose_reference_is_no_baseline_says_the_runs_it_compares_against_came_first(tmp_path):
    """fp32 against int8 (the candidate as the reference): int8's runs first read as the runs it compares against."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    clock = iter(range(1, 60))
    rb = lambda *argv: rec.rb({**Writers.HUMAN, **at(f"09:{next(clock):02d}")}, *argv)
    rb("init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb("metric", "add", "top1", "--maximize", "--unit", "%")
    rb("experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    for seed, v in enumerate([75.53, 75.58, 75.48], 1):
        rb("run", "add", "e1", f"top1={v}", "--variant", "int8", "--set", f"seed={seed}")
    rb("criterion", "add", "FP32 stays within a point of INT8", "-e", "e1", "--metric", "top1", "--subject", "fp32", "--reference", "int8",
       "--at-least", "-1", "--id", "c1")
    for seed, v in enumerate([75.69, 75.71, 75.66], 1):
        rb("run", "add", "e1", f"top1={v}", "--variant", "fp32", "--set", f"seed={seed}")
    f = rec.json(Writers.HUMAN, "show", "c1")["data"]["finding"]
    assert f["says"]["timing"] == "declared before its runs (by record times; the runs it compares against came first)", f["says"]


def test_an_id_leading_a_sentence_keeps_its_case(tmp_path):
    """FIX2 item 1: an open item led by a record's id says the id as written ("qcalib is open"), as rb status says it,
    never capitalised as a sentence start; rb's own words still start upper."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does the calibration set matter?", "--id", "qcalib")
    for tier, doc in both_tiers(root, tmp_path).items():
        said = [t for _, t in texts(doc)]
        assert not [t for t in said if t.startswith("Qcalib")], tier
        assert [t for t in said if t.startswith("qcalib is open")], tier


def test_a_unit_that_is_the_metrics_own_name_is_said_nowhere(tmp_path):
    """FIX4: a catalogue unit that is the metric's own name ("top1" for top1) is none in the document's sentences and
    the row's rule and margin, as the grid and the cards drop it: never "75.53 top1 (75.41 top1 to 75.65 top1)"."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "top1")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps 75.5 top-1", "-e", "e1", "--metric", "int8.top1", "--at-least", "75.5", "--id", "c1")
    for seed, top1 in [(1, "75.41"), (2, "75.53"), (3, "75.65")]:
        rb(you, "run", "add", "e1", f"int8.top1={top1}", "--set", f"seed={seed}")
    for tier, doc in both_tiers(root, tmp_path).items():
        f = doc["findings"]["c1"]
        assert "top1" not in f["reading_short"] + f["verdict"] + f["row"]["criterion"] + f["row"]["margin"], (tier, f["reading_short"], f["verdict"], f["row"])
        assert f["row"]["criterion"] == "≥ 75.5" and f["row"]["margin"] == "margin 0.03", (tier, f["row"])
        assert re.fullmatch(r"INT8 is 75\.53 \(\d+\.\d+ to \d+\.\d+\)", f["reading_short"]), (tier, f["reading_short"])


def test_a_reading_is_said_with_its_rules_digits(tmp_path):
    """FIX2 item 2 (R's request 126): a miss at +0.05000004 over a rule of at most 0.05 is said with the rule's digits
    on its row and in its verdict, never rounded onto the rule's number ("0.05 higher")."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 validation loss on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "val_loss", "--minimize", "--unit", "nats")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "e1", "-q", "q1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")
    rb(you, "run", "add", "e1", "int8.val_loss=1.05000004", "fp32.val_loss=1.0")
    for tier, doc in both_tiers(root, tmp_path).items():
        f = doc["findings"]["c1"]
        assert f["reading_short"].startswith("INT8 is 0.05000004 nats higher than the FP32 baseline"), (tier, f["reading_short"])
        assert "0.05000004 nats above" in f["verdict"] and "0.05 nats above" not in f["verdict"], (tier, f["verdict"])


def test_a_question_no_rule_tests_reads_one_way(tmp_path):
    """FIX2 item 3: a question no rule is linked to reads one sentence in the document, its card's line and its own
    view's alike: "No rule tests this question yet.", then what waits in its tree, in the user's voice on their own
    screens."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(CLAUDE_CODE, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    for tier, doc in both_tiers(root, tmp_path).items():
        q = next(q for q in doc["questions"] if q["id"] == "q1")
        card = next(s for s in doc["home"]["standing"] if s["question"] == "q1")
        assert q["no_rule"] == card["line"] == "No rule tests this question yet. 1 experiment design waits for your confirmation.", (tier, q["no_rule"], card["line"])


def test_a_readings_interval_never_reads_as_zero_width(tmp_path):
    """FIX2 review: a narrow interval is said with the decimals that tell its ends apart, as the verdict says it, never
    "76.55 to 76.55"."""
    root = tmp_path / "int8"
    root.mkdir()
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 keep ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps 70 top-1", "-e", "e1", "-q", "q1", "--metric", "int8.top1", "--at-least", "70", "--id", "c1")
    for seed, top1 in [(1, "76.5496"), (2, "76.5498"), (3, "76.55")]:
        rb(you, "run", "add", "e1", f"int8.top1={top1}", "--set", f"seed={seed}")
    for tier, doc in both_tiers(root, tmp_path).items():
        said = doc["findings"]["c1"]["reading_short"]
        # FIX4: a level's bounds in brackets, each with its unit
        lo, hi = re.search(r" \(([\d.]+)% to ([\d.]+)%\)$", said).groups()
        assert lo != hi, (tier, said)


def test_a_page_names_what_rb_minted_an_id_for_by_what_it_is(tmp_path):
    """FIX7: no id rb mints (a run, a note, a call, an analysis) is text a page shows. History's log rows and a finding's
    sentences say a run by its label, a note taken back by what it is on, a call by what it is and an analysis by its
    title; an id someone chose (e1, c1, a1) stays, the ids stay where a link reads them, and rb log keeps every id."""
    rec = int8_record(tmp_path / "int8")
    you = Writers.HUMAN
    note = rec.json(you, "note", "add", "the calibration set is 512 images", "-e", "e1")["data"]["object"]["id"]
    rec.rb(you, "retract", note, "--why", "written on the wrong experiment")
    runs = json.loads(subprocess.run([*command(), "status", "--json"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout)["data"]["runs"]
    first = next(r["id"] for r in runs if r.get("per_run", {}).get("seed") == 2 or "seed=2" in json.dumps(r))
    again = rec.json(you, "run", "add", "e1", "fp32.top1=75.71", "int8.top1=75.57", "--set", "seed=2", "--replaces", first, "--why",
                     "the first read calibrated on training images")["data"]["object"]["id"]
    an = rec.json(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1@pooled=-0.17[-0.29,-0.05]", "--method", "paired bootstrap over images",
                  "--unit", "image", "--units", "50000", "--reads", again, "--title", "per-image accuracy, int8 against fp32")["data"]["object"]["id"]
    # a baseline's run with no candidate's beside it, and a rule declared after the runs: its timing names that run
    lone = rec.json(you, "run", "add", "e1", "fp32.top1=75.62", "--set", "seed=9")["data"]["object"]["id"]
    rec.rb(you, "criterion", "add", "INT8 keeps top-1 within two points of FP32", "-e", "e1", "--metric", "change.top1", "--at-least", "-2", "--id", "c9")
    status = json.loads(subprocess.run([*command(), "status", "--json"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout)
    timing = next(f for f in status["data"]["findings"] if f["criterion"] == "c9")["says"]["timing"]
    assert lone in timing, ("rb status keeps the id", timing)
    # a reason someone gave keeps its words: an id in a path, in quotation marks or in a command stays as written
    odd = rec.json(you, "run", "add", "e1", "fp32.top1=75.6", "int8.top1=75.5", "--set", "seed=7")["data"]["object"]["id"]
    reason = f'out/{lone}/metrics.json is the file; quoted "{lone}"; check {lone} first; see rb show {lone}'
    rec.rb(you, "retract", odd, "--why", reason)
    log = subprocess.run([*command(), "log", "-n", "0"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout
    assert f"retract note {note} on experiment e1" in log and f"replaced by {again}" in log, "rb log keeps every id"
    minted = [note, first, again, an, lone, *re.findall(r"decision (d\w+)", log)]
    (tmp_path / "dumps").mkdir()

    def words(v):
        # the texts a reader reads as words: an id alone, which a link or a test reads, is no sentence
        if isinstance(v, dict):
            return [w for x in v.values() for w in words(x)]
        if isinstance(v, list):
            return [w for x in v for w in words(x)]
        return [v] if isinstance(v, str) and " " in v else []
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        rows = {r["sentence"] for r in doc["history"]["log"]}
        label = doc["runs"][again]["label"]
        assert "retract a note on e1: written on the wrong experiment" in rows, rows
        # the run made again has seed 2's label too: each is said with when it was recorded (FIX8)
        when = r", recorded \d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})? UTC(, the \d+(st|nd|rd|th) of \d+)?"
        assert any(re.fullmatch(f"record run {re.escape(label)}{when}: the first read calibrated on training images", r) for r in rows), rows
        assert any(re.match(f"retract run {re.escape(doc['runs'][first]['label'])}{when}: replaced by run {re.escape(label)}{when}: ", r) for r in rows), rows
        assert "analyze analysis per-image accuracy, int8 against fp32" in rows and "decide a proposed call to accept c1, not made" in rows, rows
        assert f"retract run {doc['runs'][odd]['label']}: {reason}" in rows, rows
        assert doc["runs"][odd]["retracted"]["why"] == reason and doc["runs"][odd]["retracted"]["line"].endswith(reason), doc["runs"][odd]["retracted"]
        shown = [(i, t) for t in words([doc["history"]["log"], doc["findings"]]) if reason not in t for i in minted if re.search(rf"\b{i}\b", t)]
        assert len(minted) == 6 and not shown, (tier, shown)
        elsewhere = [doc["questions"], doc["open_items"], [{k: r[k] for k in ("retracted", "receipt_rows")} for r in doc["runs"].values()]]
        shown = [(i, t) for t in words(elsewhere) if not t.startswith(("rb ", "cd ")) and reason not in t for i in minted if re.search(rf"\b{i}\b", t)]
        assert not shown, (tier, "the question's readings, open items and a run's retraction say them as the findings do", shown)
        assert any(re.search(f"replaced by run {re.escape(label)}{when}: ", t) for t in words(doc["questions"])), words(doc["questions"])
        assert f"(run {doc['runs'][lone]['label']})" in doc["findings"]["c9"]["says"]["timing"], doc["findings"]["c9"]["says"]
        assert doc["runs"][again]["run_id"] == again and an in doc["analyses"], "the ids stay where a link reads them"


def test_a_design_change_says_a_rule_by_its_metrics_title(tmp_path):
    """Look 8: a rule a design's change line says reads on the page as every other view says it, its metric by its
    title and its number with its unit ("ONNX top-1 75.5% or more"), never by its key; rb confirm and rb show keep rb's
    rule ("onnx_top1 ≥ 75.5")."""
    rec = int8_record(tmp_path / "int8")
    rec.rb(Writers.HUMAN, "metric", "add", "onnx_top1", "--maximize", "--unit", "%")
    # a session's rule, then the user's confirmation of the design as it stands, then the session's two new rules
    rec.rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 within two points", "-e", "e1", "--metric", "change.top1", "--at-least", "-2", "--id", "c3")
    listed = rec.json(Writers.HUMAN, "confirm", "e1")["data"]
    rec.rb(Writers.HUMAN, "confirm", next(d["confirm"] for d in listed["designs_pending"]).split()[-1])
    rec.rb(CLAUDE_CODE, "criterion", "add", "The ONNX export keeps 75.5 top-1", "-e", "e1", "--metric", "onnx_top1", "--at-least", "75.5", "--id", "c4")
    rec.rb(CLAUDE_CODE, "criterion", "add", "INT8 answers in 7 ms or less", "-e", "e1", "--metric", "change.top1", "--at-most", "1", "--id", "c5")
    rec.rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 on seed 1", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--where", "seed=1", "--id", "c6")
    shown = subprocess.run([*command(), "show", "e1"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout
    assert "criterion c4 added: onnx_top1 ≥ 75.5 (primary)" in shown, shown
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        [e1] = [e for e in doc["experiments"] if e["id"] == "e1"]
        lines = e1["design"]["lines"]
        assert any(l.endswith("criterion c4 added: ONNX top-1 75.5% or more (primary)") for l in lines), (tier, lines)
        assert any(l.endswith("criterion c5 added: Top-1, INT8 against the FP32 baseline: 1 point or less (primary)") for l in lines), (tier, lines)
        # a rule with a scope keeps rb's words, which say it
        [c6] = [l for l in lines if "criterion c6 added: " in l]
        assert "seed=1" in c6 and "change.top1" in c6, (tier, c6)
        assert not any("onnx_top1" in l or "change.top1" in l for l in lines if l is not c6), (tier, lines)
    # a rule changed keeps rb's words on both sides, its was being only text: c3, in the design confirmed, moves to -1.5
    crit = rec.root / ".rb" / "experiments" / "e1" / "criteria" / "c3.yaml"
    crit.write_text(re.sub(r"(?m)^target: -2(\.0)?$", "target: -1.5", crit.read_text(encoding="utf-8")), encoding="utf-8", newline="")
    rec.rb(CLAUDE_CODE, "doctor", "--adopt", "--why", "the plan says 1.5")
    (tmp_path / "dumps2").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps2").items():
        lines = next(e for e in doc["experiments"] if e["id"] == "e1")["design"]["lines"]
        assert any(l.endswith("criterion c3's rule, was change.top1 ≥ -2, now change.top1 ≥ -1.5") for l in lines), (tier, lines)


def test_a_line_names_each_run_once_and_a_proposed_call_as_proposed(tmp_path):
    """FIX8: rb's "replaced by <run>" after a session's "note: " names the run by its words; two runs whose words read
    alike are each said with when it was recorded, so no line names two runs as one; an interpretation's line keeps its
    when_met words as written, ids and all, as its text does; a call a session recorded reads as proposed, not made."""
    rec = int8_record(tmp_path / "int8")
    you = Writers.HUMAN
    runs = json.loads(subprocess.run([*command(), "status", "--json"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout)["data"]["runs"]
    first = next(r["id"] for r in runs if "seed=2" in json.dumps(r) or r.get("per_run", {}).get("seed") == 2)
    # a session replaces seed 2's run, with the same seed: two runs with one label, the retraction's reason the session's
    again = rec.json(CLAUDE_CODE, "run", "add", "e1", "fp32.top1=75.72", "int8.top1=75.57", "--set", "seed=2", "--replaces", first, "--why",
                     "the first read calibrated on training images")["data"]["object"]["id"]
    rec.rb(you, "criterion", "file", "c1", "q1", "--when-met", f"INT8 ships, as {again} shows", "--why", "the replaced run is the one to read")
    # a reason someone typed that reads like rb's words names nothing: only the run that did replace one is named
    third = next(r["id"] for r in runs if "seed=3" in json.dumps(r) or r.get("per_run", {}).get("seed") == 3)
    typed = f"note: replaced by {again}: keep these words"
    rec.rb(you, "retract", third, "--why", typed)
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        a, b = doc["runs"][first], doc["runs"][again]
        assert a["label"] == b["label"], (a["label"], b["label"])
        rows = [r["sentence"] for r in doc["history"]["log"]]
        [retract] = [r for r in rows if r.startswith("retract run ") and typed not in r]
        record = [r for r in rows if r.startswith(f"record run {a['label']}")]
        # each run said apart, with when it was recorded, and never by its id
        assert len(set(record)) == len(record) == 2, (tier, record)
        said, by = retract.split(": note: replaced by ")[0].removeprefix("retract "), retract.split(": note: replaced by ")[1].split(": ")[0]
        assert said != by and f"run {a['label']}, recorded " in said and f"run {a['label']}, recorded " in by, (tier, retract)
        # where they were recorded in one second, by their place in the order the log recorded them
        assert ", the " not in said or (said.endswith(", the 1st of 2") and by.endswith(", the 2nd of 2")), (tier, retract)
        assert not any(i in r for r in rows if typed not in r for i in (first, again)), (tier, rows)
        texts = [str(row[1]) for row in a["receipt_rows"]] + [json.dumps(a["retracted"])]
        assert any("replaced by " in t for t in texts) and not any(again in t for t in texts), (tier, texts)
        assert "decide a proposed call to accept c1, not made" in rows, (tier, rows)
        t = doc["runs"][third]
        assert t["retracted"]["why"] == typed and t["retracted"]["line"].endswith(typed), (tier, t["retracted"])
        assert any(str(row[1]).endswith(typed) for row in t["receipt_rows"]), (tier, t["receipt_rows"])
        changed = [c for q in doc["questions"] for c in q.get("changed", [])]
        assert any(c.endswith(typed) for c in changed) and any(f"replaced by run {a['label']}, recorded " in c for c in changed), (tier, changed)
        # the interpretation's words as written wherever they are said, in its line and its text alike
        dumped = json.dumps(doc, ensure_ascii=False)
        assert dumped.count("INT8 ships") == dumped.count(f"INT8 ships, as {again} shows") > 1, (tier, re.findall(r".{60}INT8 ships.{60}", dumped))


def test_an_untitled_analysis_is_an_analysis_and_each_run_is_named_as_its_lines_say_it(tmp_path):
    """FIX9: an analysis with no title reads "an analysis" in a page's lines, as a card says it, with its id for the link
    alone; runs.{id}.named (contract 1.9) is the run as every line names it, told apart from a run whose label reads the
    same."""
    rec = int8_record(tmp_path / "int8")
    runs = json.loads(subprocess.run([*command(), "status", "--json"], cwd=rec.root, env=rec.env, capture_output=True, encoding="utf-8").stdout)["data"]["runs"]
    two = next(r["id"] for r in runs if "seed=2" in json.dumps(r) or r.get("per_run", {}).get("seed") == 2)
    again = rec.json(CLAUDE_CODE, "run", "add", "e1", "fp32.top1=75.72", "int8.top1=75.57", "--set", "seed=2", "--replaces", two, "--why", "the first read calibrated on training images")["data"]["object"]["id"]
    aid = rec.json(Writers.HUMAN, "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.15[-0.31,0.01]", "--method", "paired bootstrap over images",
                   "--unit", "image", "--units", "50000", "--reads", again)["data"]["object"]["id"]
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        assert doc["analyses"][aid]["title"] == "", (tier, doc["analyses"][aid])
        rows = [r["sentence"] for r in doc["history"]["log"]]
        assert any(r.startswith("analyze an analysis") for r in rows), (tier, rows)
        assert not any(aid in r for r in rows), (tier, [r for r in rows if aid in r])
        assert not any("analysis an analysis" in r for r in rows), (tier, rows)
        named = {rid: r["named"] for rid, r in doc["runs"].items()}
        assert set(named) == set(doc["runs"]) and all(n.startswith(("run ", "a run of ")) and rid not in n for rid, n in named.items()), (tier, named)
        # two runs with one label are named apart, and each is named as its log row says it
        assert len(set(named.values())) == len(named), (tier, named)
        for rid, n in named.items():
            assert any(r == f"record {n}" or r.startswith(f"record {n}: ") for r in rows), (tier, rid, n, rows)
