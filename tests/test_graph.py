"""What rests on what (BP 1a-1, graph.rs): one rule, by ids, that rb retract words and the shell's document hands
apart. Every edge the document gives of a record is the one rb retract names of it, for every kind of record."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from rbtest import CLAUDE_CODE, Writers
from test_shell_model import both_tiers


def graph_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, written by the user (human:ana, from git) unless a session is named: q1 with a
    sub-question q1a; e1 (int8 against fp32, three seeds, frozen) with c1 (linked to q1, with its interpretation) and
    c2 (linked to q1a, declared to reverse c1); runs that read imagenet-val, the first making the checkpoint ckpt-int8
    and keeping its bytes, the third superseded for its wall clock alone, and a fourth taken back whole; analysis a1
    over all four, which c1 and c2 read, and a2 over a1; e2 (calibration set size, a design
    factor) with c3 over its cells, c4 an --each rule and c5 a session declared with its interpretation, one run there
    holding the checkpoint's bytes and superseded for its latency alone, and a cell with the baseline alone; e3, a run
    recorded before its spec changed, c6 declared after, and a run holding the checkpoint's bytes taken back whole; a call the user made on c1, and a note."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rb(you, "question", "add", "Does the calibration set size matter?", "--parent", "q1", "--id", "q1a")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation", "--url", "https://www.image-net.org/")
    rb(you, "resource", "add", "ckpt-int8", "--type", "checkpoint", "--name", "ResNet-50 INT8")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point of FP32", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-1",
       "--when-met", "INT8 is accurate enough to ship", "--id", "c1")
    rb(you, "criterion", "add", "INT8 keeps top-1 within half a point", "-e", "e1", "-q", "q1a", "--metric", "change.top1", "--at-least", "-0.5",
       "--id", "c2")
    rb(you, "criterion", "relate", "c2", "reverses", "c1", "--orientation", "same", "--why", "the same comparison read the other way")
    (root / "ckpt").mkdir()
    (root / "ckpt" / "int8.pt").write_bytes(b"int8 weights, seed 1\n")
    runs = []
    for seed, (fp32, int8) in enumerate([(75.69, 75.53), (75.71, 75.58), (75.66, 75.48)], 1):
        extra = {1: ["--output", "ckpt-int8:checkpoint", "--artifact", "ckpt/int8.pt"], 3: ["fp32.wall_s=41.0", "int8.wall_s=23.5"]}.get(seed, [])
        made = rec.json(you, "run", "add", "e1", f"fp32.top1={fp32}", f"int8.top1={int8}", *extra, "--set", f"seed={seed}", "--input",
                        "imagenet-val:eval")
        runs.append(made["data"]["object"]["id"])
    rb(you, "freeze", "e1", "-m", "before the analysis")
    wrong = rec.json(you, "run", "add", "e1", "fp32.top1=75.70", "int8.top1=72.10", "--set", "seed=4", "--input", "imagenet-val:eval")["data"]["object"]["id"]
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.16[-0.21,-0.11]", "--method", "paired bootstrap over images", "--unit", "image",
       "--units", "50000", "--reads", ",".join([*runs, wrong]), "--id", "a1")
    rb(you, "retract", wrong, "--why", "evaluated on the train split")
    rb(you, "retract", runs[2], "--why", "the wall clock was wrong", "--scope", "wall_s")
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1@pooled=-0.17[-0.29,-0.05]", "--method", "pooled over a1", "--reads", "a1", "--id", "a2")
    rb(you, "experiment", "add", "calibration set size", "--id", "e2", "-q", "q1a", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e2", "calib_images", "--design-factor")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point at every calibration size", "-e", "e2", "-q", "q1a", "--metric", "change.top1",
       "--at-least", "-1", "--id", "c3")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point in every unit", "-e", "e2", "-q", "q1a", "--metric", "change.top1", "--at-least",
       "-1", "--each", "--id", "c4")
    rb(CLAUDE_CODE, "criterion", "add", "INT8 keeps top-1 within two points at every size", "-e", "e2", "-q", "q1a", "--metric", "change.top1",
       "--at-least", "-2", "--when-met", "the calibration set size does not matter", "--id", "c5")
    cells = []
    for images, (fp32, int8) in [(128, (75.69, 74.31)), (512, (75.69, 75.40))]:
        extra = ["--artifact", "ckpt/int8.pt"] if images == 512 else []
        made = rec.json(you, "run", "add", "e2", f"fp32.top1={fp32}", f"int8.top1={int8}", f"fp32.latency_ms=4.1", f"int8.latency_ms=2.2", "--set",
                        f"calib_images={images}", "--input", "imagenet-val:eval", *extra)
        cells.append(made["data"]["object"]["id"])
    rb(you, "retract", cells[1], "--why", "the latency clock was wrong", "--scope", "latency_ms")
    # a cell with the baseline alone, so no complete comparison
    half = rec.json(you, "run", "add", "e2", "fp32.top1=75.69", "--set", "calib_images=2048")["data"]["object"]["id"]
    # a run recorded before its experiment's spec changed, and a criterion declared after both
    rb(you, "experiment", "add", "batch size", "--id", "e3", "-q", "q1a", "--baseline", "fp32", "--candidate", "int8")
    early = rec.json(you, "run", "add", "e3", "fp32.top1=75.6", "int8.top1=75.5")["data"]["object"]["id"]
    rb(you, "spec", "set", "e3", "batch", "256", "--no-verify")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point at batch 256", "-e", "e3", "-q", "q1a", "--metric", "change.top1", "--at-least",
       "-1", "--id", "c6")
    # a run of e3 holding the checkpoint's bytes, taken back whole
    gone = rec.json(you, "run", "add", "e3", "fp32.top1=75.6", "int8.top1=75.1", "--artifact", "ckpt/int8.pt")["data"]["object"]["id"]
    rb(you, "retract", gone, "--why", "the wrong checkpoint")
    call = rec.json(you, "decide", "c1", "accept", "--why", "within a point on three seeds", "--words", "ship it once seed 3 lands")["data"]["object"]["id"]
    note = rec.json(you, "note", "add", "the eval host ran offline", "-e", "e1")["data"]["object"]["id"]
    rec.ids.update(runs=runs, cells=cells, wrong=wrong, call=call, note=note, half=half, early=early, gone=gone)
    return rec


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    return graph_record(tmp_path_factory.mktemp("graph") / "int8")


@pytest.fixture(scope="module")
def graph_docs(graph, tmp_path_factory):
    return both_tiers(graph.root, tmp_path_factory.mktemp("graph-dumps"))


def rested_on(rec: Writers, subject: str) -> list:
    """What rb retract would name of `subject`, written nowhere."""
    return rec.json(Writers.HUMAN, "retract", subject, "--why", "a look", "--dry-run")["data"]["rested_on"]


def named(words: list, pattern: str) -> list:
    """The ids rb retract's words name in the place `pattern`'s {} holds, in their order."""
    head, tail = pattern.split("{}")
    return [w[len(head):len(w) - len(tail)] for w in words if w.startswith(head) and w.endswith(tail) and len(w) > len(head) + len(tail)]


def test_rests_on_is_one_rule(graph, graph_docs):
    """rb retract names what rests on a record of every kind, and the edges the shell's document hands apart by id are
    the ones those words name: a run's criteria (counted, or read in a timing) and the analyses that read it, an
    analysis's readers, a resource's runs, a criterion's question links and relations both ways. Its words are rb
    retract's of before graph.rs but for two changes, each one rule with rb show's: an experiment and a resource name a
    run superseded for one measurement, which still stands, and a resource names the runs that read it before those
    that made it."""
    (r1, r2, r3), (cell, superseded), call, note = graph.ids["runs"], graph.ids["cells"], graph.ids["call"], graph.ids["note"]
    half, early = graph.ids["half"], graph.ids["early"]
    expected = {
        "q1": ["experiment e1", "question q1a", "criterion c1 is linked to it"],
        "q1a": ["experiment e2", "experiment e3", *[f"criterion {c} is linked to it" for c in ("c2", "c3", "c4", "c5", "c6")]],
        "e1": ["it is frozen", f"run {r1}", f"run {r2}", f"run {r3}", "criterion c1 is linked to question q1", "criterion c2 is linked to question q1a",
               "criterion c2 reverses criterion c1"],
        "e2": [f"run {cell}", f"run {superseded}", f"run {half}", *[f"criterion {c} is linked to question q1a" for c in ("c3", "c4", "c5")]],
        "e3": [f"run {early}", "criterion c6 is linked to question q1a"],
        "c1": [f"decision {call}", "experiment e1 is frozen", "it is a primary criterion and evidence reads on it", "it is linked to question q1",
               "criterion c2 reverses it"],
        "c2": ["experiment e1 is frozen", "it is a primary criterion and evidence reads on it", "it is linked to question q1a", "it reverses criterion c1"],
        "c3": ["it is a primary criterion and evidence reads on it", "it is linked to question q1a"],
        "c6": ["it is linked to question q1a"],
        "a1": ["criterion c1 reads it", "criterion c2 reads it", "analysis a2 read it"],
        "a2": [],
        r1: ["analysis a1 read it", "experiment e1 is frozen"],
        r2: ["analysis a1 read it", "experiment e1 is frozen"],
        cell: ["criterion c3 counts it", "criterion c4 counts it", "criterion c5 counts it"],
        early: ["criterion c6 reads it in its timing"],
        "imagenet-val": [f"run {r1}", f"run {r2}", f"run {r3}", f"run {cell}", f"run {superseded}"],
        "ckpt-int8": [f"run {r1}"],
        "top1": [f"criterion {c} reads it" for c in ("c1", "c2", "c3", "c4", "c5", "c6")],
        "e1/seed": ["experiment e1 is frozen"],
        "e1/fp32": ["e1 is frozen"],
        call: [],
        note: [],
    }
    said = {subject: rested_on(graph, subject) for subject in expected}
    assert said == expected
    for tier in ("live", "owner"):
        doc = graph_docs[tier]
        for run in (r1, r2, cell, early):
            toward = doc["runs"][run]["counts_toward"]
            assert [t["criterion"] for t in toward if t["counted"] and not t["via"]] == named(said[run], "criterion {} counts it")
            # read in its timing: observed, not counted, and produced before the criterion was declared
            assert [t["criterion"] for t in toward if not t["counted"] and not t["via"]] == named(said[run], "criterion {} reads it in its timing")
            reading = [a for a, x in doc["analyses"].items() if run in x["reads"]]
            assert reading == named(said[run], "analysis {} read it")
        for a in ("a1", "a2"):
            assert doc["analyses"][a]["used_by"] == named(said[a], "criterion {} reads it") + named(said[a], "analysis {} read it")
        for res in ("imagenet-val", "ckpt-int8"):
            x = doc["resources"][res]
            runs = [u["run"] for u in x["read_by"]["runs"] + x["made_by"]]
            assert list(dict.fromkeys(runs)) == named(said[res], "run {}")
        for c in ("c1", "c2", "c3", "c6"):
            rests = doc["findings"][c]["rests_on"]
            assert rests["question_links"] == named(said[c], "it is linked to question {}")
            assert [f"it {x['kind']} criterion {x['criterion']}" for x in rests["relations_out"]] == [w for w in said[c] if w.startswith("it reverses")]
            assert [f"criterion {x['criterion']} {x['kind']} it" for x in rests["relations_in"]] == [w for w in said[c] if re.fullmatch(r"criterion \S+ reverses it", w)]


def test_a_resource_is_read_or_made(graph, graph_docs):
    """A resource says apart the runs that read it and those that made it, each with its experiment, a run superseded
    for one measurement among them; the analyses that read it themselves or through what they read; and the criteria
    resting on what read it, by the questions they are linked to. rb show says the same runs."""
    (r1, r2, r3), (cell, superseded) = graph.ids["runs"], graph.ids["cells"]
    for tier in ("live", "owner"):
        doc = graph_docs[tier]
        val, ckpt = doc["resources"]["imagenet-val"], doc["resources"]["ckpt-int8"]
        assert val["read_by"] == {"runs": [{"run": r, "experiment": e} for r, e in [(r1, "e1"), (r2, "e1"), (r3, "e1"), (cell, "e2"), (superseded, "e2")]],
                                  "analyses": ["a1", "a2"]}
        assert val["made_by"] == []
        assert val["rests_on"] == {"criteria": ["c1", "c2", "c3", "c4", "c5"], "by_question": {"q1": ["c1"], "q1a": ["c2", "c3", "c4", "c5"]}}
        assert ckpt["read_by"] == {"runs": [], "analyses": []} and ckpt["made_by"] == [{"run": r1, "experiment": "e1"}]
        assert ckpt["rests_on"] == {"criteria": [], "by_question": {}}
    shown = graph.rb(Writers.HUMAN, "show", "imagenet-val")
    assert [line.split()[3] for line in shown.splitlines() if line.startswith("  read by run ")] == [r1, r2, r3, cell, superseded]
    assert "read by analysis a1 (e1), through what it read" in shown and "read by analysis a2 (e1), through what it read" in shown
    assert f"made by run {r1} (e1" in graph.rb(Writers.HUMAN, "show", "ckpt-int8")


def test_a_run_counts_toward_and_holds_the_same_bytes(graph, graph_docs):
    """A run says each criterion it counts toward, directly or through the analysis whose reading the criterion reads,
    a run taken back whole not counted there with rb's reason, one superseded for a measurement still counted; the
    other runs holding a file of the same bytes, whether each was taken back whole; and what it made."""
    (r1, r2, r3), (cell, superseded), wrong, gone = graph.ids["runs"], graph.ids["cells"], graph.ids["wrong"], graph.ids["gone"]
    for tier in ("live", "owner"):
        runs = graph_docs[tier]["runs"]
        assert runs[r1]["counts_toward"] == [{"criterion": c, "counted": True, "reason": "", "via": "a1"} for c in ("c1", "c2")]
        assert runs[wrong]["counts_toward"] == [{"criterion": c, "counted": False, "reason": "retracted: evaluated on the train split", "via": "a1"}
                                                for c in ("c1", "c2")]
        assert runs[superseded]["counts_toward"] == [{"criterion": c, "counted": True, "reason": "", "via": ""} for c in ("c3", "c4", "c5")]
        assert runs[r3]["counts_toward"] == [{"criterion": c, "counted": True, "reason": "", "via": "a1"} for c in ("c1", "c2")]
        assert runs[graph.ids["early"]]["counts_toward"] == [{"criterion": "c6", "counted": False, "reason": "under an earlier spec: batch unknown -> 256", "via": ""}]
        assert runs[r1]["same_bytes"] == [{"run": superseded, "experiment": "e2", "retracted": False, "made": []},
                                          {"run": gone, "experiment": "e3", "retracted": True, "made": []}]
        assert runs[superseded]["same_bytes"] == [{"run": r1, "experiment": "e1", "retracted": False, "made": ["ckpt-int8"]},
                                                  {"run": gone, "experiment": "e3", "retracted": True, "made": []}]
        assert runs[r2]["same_bytes"] == [] and runs[cell]["same_bytes"] == []
        assert runs[r1]["made"] == ["ckpt-int8"] and runs[r2]["made"] == []


def said_value(v) -> str:
    """A per-run value as rb says it: true and false as rb writes them."""
    return json.dumps(v) if isinstance(v, bool) else str(v)


def test_a_finding_carries_its_observations_source_and_cells(graph, graph_docs):
    """A finding hands apart what rb show prints of it: each observation with its run, arm, unit, per-run values, value
    in rb's digits, whether it counts and why not; where its reading comes from (the runs, or an analysis); and each
    design cell with its own estimate, its runs and whether it falls outside the rule, judged unit by unit for an
    --each rule, a cell with no complete comparison listed with no estimate."""
    (cell, superseded), half = graph.ids["cells"], graph.ids["half"]
    shown = graph.json(Writers.HUMAN, "show", "c3")["data"]["finding"]
    for tier in ("live", "owner"):
        f = graph_docs[tier]["findings"]
        assert f["c3"]["observations"] == [{"run": o["run"], "arm": o["arm"], "unit": o["unit"], "per_run": {k: said_value(v) for k, v in o["per_run"].items()},
                                            "value_text": v, "counted": o["counted"], "reason": o["reason"]}
                                           for o, v in zip(shown["observations"], ["-1.38", "-0.29", "75.69"])]
        assert [(o["unit"], o["counted"]) for o in f["c3"]["observations"]] == [("calib_images=128", True), ("calib_images=512", True), ("calib_images=2048", False)]
        assert f["c3"]["source"] == {"kind": "runs", "id": ""} and f["c1"]["source"] == {"kind": "analysis", "id": "a1"}
        # each cell's own estimate, never the mean across cells (-0.835)
        # one unit a cell: no interval; the cell with no complete comparison has no units the reading counts
        one = {"interval_text": "", "n": 1, "unit": "calib_images"}
        cells = [{"key": "calib_images=128", "estimate_text": "-1.38", "runs": [cell], "outside": True, **one},
                 {"key": "calib_images=512", "estimate_text": "-0.29", "runs": [superseded], "outside": False, **one},
                 {"key": "calib_images=2048", "estimate_text": "", "runs": [half], "outside": False, "interval_text": "", "n": 0, "unit": ""}]
        assert f["c3"]["design_cells"] == cells and f["c4"]["design_cells"] == cells
        # a rule two points wide: no cell outside it
        assert [x["outside"] for x in f["c5"]["design_cells"]] == [False, False, False]
        assert f["c1"]["design_cells"] == []
    assert "-1.38 to -0.29" in graph.rb(Writers.HUMAN, "show", "c3")


def test_what_rests_on_a_finding(graph, graph_docs):
    """A finding says what rests on it: the interpretations declared with it, confirmed or not (a session's is not),
    and not active while the analysis its reading comes from rests on a run taken back, or while its rule is not met;
    its question links; the relations it declares and those declared to it; and the call made on it, the user's words
    on the Live tier only."""
    for tier in ("live", "owner"):
        f = graph_docs[tier]["findings"]
        quote = ' · "ship it once seed 3 lands"' if tier == "live" else ""
        rests = dict(f["c1"]["rests_on"])
        assert re.fullmatch(r"accepted \d{4}-\d\d-\d\d: within a point on three seeds" + re.escape(quote), rests.pop("call"))
        assert rests == {"interpretations": [{"question": "q1", "active": False, "confirmed": True, "text": "INT8 is accurate enough to ship"}],
                         "question_links": ["q1"], "relations_out": [], "relations_in": [{"kind": "reverses", "criterion": "c2"}]}
        assert f["c2"]["rests_on"] == {"interpretations": [], "question_links": ["q1a"], "relations_out": [{"kind": "reverses", "criterion": "c1"}],
                                       "relations_in": [], "call": ""}
        assert f["c5"]["rests_on"]["interpretations"] == [{"question": "q1a", "active": False, "confirmed": False,
                                                           "text": "the calibration set size does not matter"}]


def small_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, by the user: e1 with c1, three runs that read imagenet-val, read from rows of one
    SQLite database, an analysis a1 over them and a2 over a1, which c1 reads, and a run a session recorded with its note
    and took back."""
    import sqlite3
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    db = sqlite3.connect(root / "results.db")
    db.execute("create table evals (seed integer, fp32_top1 real, int8_top1 real)")
    db.executemany("insert into evals values (?, ?, ?)", [(1, 75.69, 75.53), (2, 75.71, 75.58), (3, 75.66, 75.48)])
    db.commit()
    db.close()
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    rb(you, "metric", "add", "fp32_top1", "--maximize")
    rb(you, "metric", "add", "int8_top1", "--maximize")
    rb(you, "experiment", "add", "int8 against fp32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8")
    rb(you, "spec", "set", "e1", "seed", "--per-run")
    rb(you, "criterion", "add", "INT8 keeps top-1 within a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    rb(you, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet-1k validation")
    runs = [rec.json(you, "run", "add", "e1", "--from", "results.db", "--table", "evals", "--where", f"seed={s}", "--set", "seed=@seed", "--input",
                     "imagenet-val:eval")["data"]["object"]["id"] for s in (1, 2, 3)]
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.16[-0.21,-0.11]", "--method", "paired bootstrap over images", "--reads",
       ",".join(runs), "--id", "a1")
    rb(you, "analysis", "add", "e1", "--compare", "int8/fp32:top1=-0.15[-0.2,-0.1]", "--method", "the same bootstrap, read again", "--reads", "a1",
       "--id", "a2")
    # a session's run, with its note, and its retraction
    rec.ids["session"] = rec.json(CLAUDE_CODE, "run", "add", "e1", "fp32.top1=75.70", "int8.top1=75.20", "--set", "seed=4", "--note",
                                  "the eval host was warm")["data"]["object"]["id"]
    rb(CLAUDE_CODE, "retract", rec.ids["session"], "--why", "a stale build")
    rec.ids["runs"] = runs
    return rec


def test_rows_of_one_database_are_not_the_same_bytes_and_an_edited_run_is_not_counted(tmp_path):
    """Runs read from rows of one database share its file, never their bytes; and a run changed outside rb is said not
    counted under the analysis that read it, with the reason its observations give."""
    rec = small_record(tmp_path / "int8")
    r1, r2, _ = rec.ids["runs"]
    path = next((rec.root / ".rb" / "experiments" / "e1" / "runs").glob(f"{r2}.yaml"))
    path.write_text(path.read_text(encoding="utf-8").replace("seed: 2", "seed: 7"), encoding="utf-8")
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        runs = doc["runs"]
        assert [runs[r]["same_bytes"] for r in rec.ids["runs"]] == [[], [], []]
        assert runs[r1]["counts_toward"] == [{"criterion": "c1", "counted": True, "reason": "", "via": "a2"}]
        assert runs[r2]["counts_toward"] == [{"criterion": "c1", "counted": False, "reason": "edited outside rb", "via": "a2"}]


def test_a_sessions_run_note_and_retraction_read_as_its_notes(tmp_path):
    """A run's note and the reason it was taken back, where a session gave them, read as not confirmed, as the run's
    card says them; the user's own read as written."""
    rec = small_record(tmp_path / "int8")
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        run = doc["runs"][rec.ids["session"]]
        assert run["note"] == "the eval host was warm · not confirmed"
        assert run["retracted"]["why"] == "a stale build · not confirmed" and run["retracted"]["line"] == "retracted · note: a stale build · not confirmed"
        assert doc["runs"][rec.ids["runs"][0]]["note"] == ""


def test_what_rests_on_a_resource_reads_only_its_runs(tmp_path):
    """rb retract of a resource names the runs that read or made it, and reads nothing else: an analysis no longer a
    valid record leaves it as it was."""
    rec = small_record(tmp_path / "int8")
    before = rested_on(rec, "imagenet-val")
    assert before == [f"run {r}" for r in rec.ids["runs"]]
    (rec.root / ".rb" / "experiments" / "e1" / "analyses" / "a1.yaml").write_text("rb: 1\nkind: analysis\nid: [\n", encoding="utf-8")
    assert rested_on(rec, "imagenet-val") == before


def test_an_analysis_changed_outside_rb_is_read_as_rb_wrote_it(tmp_path):
    """An analysis changed outside rb is not read as it stands: what it read is what rb last wrote, both in what rb
    retract names and in the document, and the runs it rests on read as not counted there, with rb's reason."""
    rec = small_record(tmp_path / "int8")
    r1, r2, r3 = rec.ids["runs"]
    a1 = rec.root / ".rb" / "experiments" / "e1" / "analyses" / "a1.yaml"
    a1.write_text("".join(line for line in a1.read_text(encoding="utf-8").splitlines(keepends=True) if r3 not in line), encoding="utf-8")
    assert rested_on(rec, r3) == ["analysis a1 read it"]
    (tmp_path / "dumps").mkdir()
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        assert [doc["runs"][r]["counts_toward"] for r in (r1, r3)] == [[{"criterion": "c1", "counted": False, "reason": "edited outside rb", "via": "a2"}]] * 2


def test_a_shallow_clone_says_why_it_cannot_read_a_run(tmp_path):
    """In a shallow clone, a run whose file git's history cannot account for reads as not counted with the shallow
    clone's words, under the analysis that read it as under the criterion itself."""
    rec = small_record(tmp_path / "int8")
    r2 = rec.ids["runs"][1]
    rec.git("add", "-A")
    rec.git("commit", "-q", "-m", "the record")
    path = rec.root / ".rb" / "experiments" / "e1" / "runs" / f"{r2}.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("seed: 2", "seed: 7"), encoding="utf-8")
    rec.git("commit", "-q", "-am", "an edit")
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{rec.root}", str(shallow)], check=True, env=rec.env)
    (tmp_path / "dumps").mkdir()
    doc = both_tiers(shallow, tmp_path / "dumps")["owner"]
    assert doc["runs"][r2]["counts_toward"] == [{"criterion": "c1", "counted": False, "reason": "the clone is shallow", "via": "a2"}]


def cells_record(root: Path) -> Writers:
    """INT8 ResNet-50 on ImageNet, by the user: e1 (calibration set size a design factor, seed per run) with c1, an
    --each rule within a point, its 128-image cell executed twice for one seed and its 512-image cell repeated; e2 the
    same comparison with each arm recorded in a run of its own, a 2048-image cell with the baseline alone, and one more
    baseline run there whose config disagrees with its seed."""
    root.mkdir(parents=True)
    rec = Writers(root)
    you, rb = Writers.HUMAN, rec.rb
    rec.git("init", "-q")
    rec.git("config", "user.email", "ana@example.org")
    rec.git("config", "user.name", "ana")
    rb(you, "init", "INT8 ResNet-50 on ImageNet", "--no-agents")
    rb(you, "metric", "add", "top1", "--maximize", "--unit", "%")
    for e in ("e1", "e2"):
        rb(you, "experiment", "add", f"calibration set size, {e}", "--id", e, "--baseline", "fp32", "--candidate", "int8")
        rb(you, "spec", "set", e, "calib", "--design-factor")
        rb(you, "spec", "set", e, "seed", "--per-run")
        rb(you, "criterion", "add", "INT8 keeps top-1 within a point in every unit", "-e", e, "--metric", "change.top1", "--at-least", "-1", "--each",
           "--id", f"c{e[1]}")
    rb(you, "run", "add", "e1", "fp32.top1=75.0", "int8.top1=74.5", "--set", "calib=128", "--set", "seed=1")
    rb(you, "run", "add", "e1", "fp32.top1=75.0", "int8.top1=73.7", "--set", "calib=128", "--set", "seed=1", "--again", "--why", "a second execution")
    first = rec.json(you, "run", "add", "e1", "fp32.top1=75.0", "int8.top1=74.8", "--set", "calib=512", "--set", "seed=1")["data"]["object"]["id"]
    rb(you, "run", "add", "e1", "fp32.top1=75.0", "int8.top1=73.9", "--set", "calib=512", "--set", "seed=1", "--repeats", first)
    for calib, (fp32, int8) in [(128, (75.69, 74.31)), (512, (75.69, 75.40))]:
        rb(you, "run", "add", "e2", f"top1={fp32}", "--variant", "fp32", "--set", f"calib={calib}", "--set", "seed=1")
        rb(you, "run", "add", "e2", f"top1={int8}", "--variant", "int8", "--set", f"calib={calib}", "--set", "seed=1")
    half = rec.json(you, "run", "add", "e2", "top1=75.69", "--variant", "fp32", "--set", "calib=2048", "--set", "seed=1")["data"]["object"]["id"]
    (root / "cfg.yaml").write_text("calib: 2048\nseed: 9\n", encoding="utf-8")
    rb(you, "run", "add", "e2", "top1=75.70", "--variant", "fp32", "--set", "calib=2048", "--set", "seed=2", "--config", "cfg.yaml")
    rec.ids["half"] = half
    return rec


def test_design_cells_are_judged_as_the_rule_judges_them(tmp_path):
    """An --each rule's cell is outside the rule only where a unit in it is, each unit's runs averaged as the rule reads
    them (a second execution and a repeat in one unit with the first); with each arm in a run of its own, a cell with no
    complete comparison leaves the others judged, and lists only the runs the reading reads; each as rb show says."""
    rec = cells_record(tmp_path / "int8")
    (tmp_path / "dumps").mkdir()
    shown = {c: rec.json(Writers.HUMAN, "show", c)["data"]["finding"] for c in ("c1", "c2")}
    assert shown["c1"]["rule_outcome"] == "meets" and shown["c2"]["comparison"]["design"]["meets"] == 1
    for tier, doc in both_tiers(rec.root, tmp_path / "dumps").items():
        f = doc["findings"]
        assert [(x["key"], x["outside"]) for x in f["c1"]["design_cells"]] == [("calib=128", False), ("calib=512", False)]
        assert [(x["key"], x["estimate_text"], x["outside"]) for x in f["c2"]["design_cells"]] == [("calib=128", "-1.38", True), ("calib=512", "-0.29", False),
                                                                                                    ("calib=2048", "", False)]
        assert f["c2"]["design_cells"][2]["runs"] == [rec.ids["half"]]
