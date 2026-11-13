"""PLAIN.md, the author's plain lines (UX3 4.2, D1 (b)): a file beside .rb/ that gives each name the record uses one line in
plain words. rb reads it and writes nothing; the overview model carries its lines as the file's words (`voice: file`,
with their place in the file), and the same on rb status --json, the view rb page builds and rb open; a line for a name the
record does not hold is reported, never an error, and rb doctor names it. An older rb reads a record beside it as it
reads one without it (RB_OLD_BIN)."""
import json
import os
import subprocess
import urllib.request

import pytest

from rbtest import Open, Recorder

PLAIN = """# Plain words for this project

## project
Can we make an image program smaller and faster without making it worse?

## q1
Does the smaller copy recognize pictures almost as well as the original?

## Variants

- fp32: the original program, which keeps its numbers in full detail
- `int8`: the smaller copy, which keeps its numbers with less detail

## top1
How often the program's first guess about a picture is right, in percent.

## setting seed
Which random start a try used.

## e-old
An experiment that was renamed since.
"""


def record(tmp_path, name="plain") -> Recorder:
    rec = Recorder(tmp_path, name)
    rec.start()
    rec.write("README.md", "# INT8 ResNet-50\n")
    rec.git("add", "-A")
    rec.git("commit", "-qm", "start")
    rec.rb("init", "INT8 ResNet-50", "--no-agents")
    rec.rb("question", "add", "Does INT8 cost ResNet-50 top-1 on ImageNet?", "--id", "q1")
    rec.rb("question", "add", "Does the calibration set size matter?", "--id", "q1a", "--parent", "q1")
    rec.rb("question", "add", "Is INT8 faster?", "--id", "q2")
    rec.rb("metric", "add", "top1", "--maximize", "--unit", "%")
    rec.rb("experiment", "add", "int8 against fp32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8",
           "--varies", "precision", "--hypothesis", "INT8 keeps top-1 within a point of FP32")
    rec.rb("spec", "set", "e1", "seed", "--per-run")
    rec.rb("criterion", "add", "INT8 loses at most one point of top-1", "-e", "e1", "-q", "q1", "--metric", "change.top1",
           "--at-least", "-1", "--id", "c1")
    for seed, (f, i) in enumerate([(76.1, 75.9), (76.0, 75.8), (76.2, 76.0)], 1):
        rec.rb("run", "add", "e1", f"fp32.top1={f}", f"int8.top1={i}", "--set", f"seed={seed}", "--produced-at", "unknown")
    return rec


def with_plain(rec, text=PLAIN):
    rec.write("PLAIN.md", text)
    rec.git("add", "-A")
    rec.git("commit", "-qm", "plain lines")


def record_bytes(rec) -> dict:
    out = {}
    for p in sorted((rec.root / ".rb").rglob("*")):
        if p.is_file() and "cache" not in p.relative_to(rec.root / ".rb").parts and "journal" not in p.relative_to(rec.root / ".rb").parts:
            out[str(p.relative_to(rec.root))] = p.read_bytes()
    return out


def test_no_file_means_no_line(tmp_path):
    rec = record(tmp_path)
    ov = rec.rb("status")["overview"]
    assert ov["plain"] is None
    assert ov["question"]["plain"] is None and ov["question"]["plain_cite"] is None
    assert all(o["plain"] is None for o in ov["others"])
    assert all(c["check"] != "plain_lines" for c in rec.rb("doctor")["checks"])


def test_each_line_is_the_files_words_with_its_place(tmp_path):
    rec = record(tmp_path)
    before = record_bytes(rec)
    with_plain(rec)
    st = rec.rb("status")
    ov = st["overview"]
    assert ov["question"]["id"] == "q1"
    assert ov["question"]["plain"] == "Does the smaller copy recognize pictures almost as well as the original?"
    assert ov["question"]["plain_cite"] == "PLAIN.md:7"
    assert ov["question"]["text"] == "Does INT8 cost ResNet-50 top-1 on ImageNet?"
    plain = ov["plain"]
    assert plain["file"] == "PLAIN.md" and plain["voice"] == "file"
    assert plain["project"]["text"].startswith("Can we make an image program") and plain["project"]["cite"] == "PLAIN.md:4"
    lines = {(x["kind"], x["id"], x["experiment"]): x for x in plain["lines"]}
    assert lines[("variant", "int8", "e1")]["text"] == "the smaller copy, which keeps its numbers with less detail"
    assert lines[("variant", "fp32", "e1")]["cite"] == "PLAIN.md:11"
    assert lines[("metric", "top1", "")]["line"] == 15
    assert lines[("setting", "seed", "e1")]["text"] == "Which random start a try used."
    assert all(x["voice"] == "file" and x["file"] == "PLAIN.md" for x in plain["lines"])
    # a question no line names has none: nothing is made up for it
    assert next(o for o in ov["others"] if o["id"] == "q2")["plain"] is None
    # what the record does not hold is reported, never an error
    assert [(u["about"], u["line"]) for u in plain["unknown"]] == [("e-old", 21)]
    # rb read it and wrote nothing
    assert record_bytes(rec) == before


def test_rb_doctor_names_a_line_whose_name_is_gone(tmp_path):
    rec = record(tmp_path)
    with_plain(rec)
    data = rec.rb("doctor")
    check = next(c for c in data["checks"] if c["check"] == "plain_lines")
    assert check["ok"] is True and "e-old (PLAIN.md:21)" in check["detail"]
    assert data["outcome"]["passed"] is True
    # a name taken back from the record is gone too
    rec.rb("retract", "q2", "--why", "asked twice")
    with_plain(rec, PLAIN + "\n## q2\nIs the smaller copy quicker?\n")
    check = next(c for c in rec.rb("doctor")["checks"] if c["check"] == "plain_lines")
    assert "q2 (PLAIN.md:24)" in check["detail"] and "e-old" in check["detail"]


def test_every_surface_holds_the_same_lines(tmp_path):
    rec = record(tmp_path)
    with_plain(rec)
    ov = rec.rb("status")["overview"]
    dump = tmp_path / "page.view.json"
    rec.env = {**rec.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    rec.rb("page", "--out", str(tmp_path / "page.html"))
    rec.env = {k: v for k, v in rec.env.items() if not k.startswith("RB_TEST_SHELL")}
    assert json.loads(dump.read_text(encoding="utf-8"))["overview"]["plain"] == ov["plain"]
    old, saved = os.getcwd(), dict(os.environ)
    os.environ.update(rec.env)
    try:
        srv = Open(rec.root)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(srv.url + "/state.json", timeout=60) as r:
                home = json.loads(r.read().decode("utf-8"))["home"]
        finally:
            srv.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        os.chdir(old)
    assert home["overview"]["plain"] == ov["plain"] and home["overview"]["question"] == ov["question"]


def test_a_hand_written_file_is_read_forgivingly(tmp_path):
    rec = record(tmp_path)
    with_plain(rec, "---\nfor: readers\n---\n<!-- written by hand -->\n# PROJECT:\n  An image program,\n  made smaller.\n\n"
               "```\n## q1\nnot a line\n```\n### **Q1**\nThe first question.\n\n* experiment e1 - one try of the smaller copy\n"
               "- e1/fp32 — the original\n- a sentence: that names nothing\n## Notes\n")
    plain = rec.rb("status")["overview"]["plain"]
    lines = {(x["kind"], x["id"]): (x["text"], x["line"]) for x in plain["lines"]}
    assert lines[("project", "project")] == ("An image program, made smaller.", 6)
    assert lines[("question", "q1")] == ("The first question.", 14)
    assert lines[("experiment", "e1")] == ("one try of the smaller copy", 16)
    assert lines[("variant", "fp32")] == ("the original", 17)
    assert plain["unknown"] == [] and plain["twice"] == []


@pytest.mark.skipif(not os.environ.get("RB_OLD_BIN"), reason="RB_OLD_BIN names an rb from before PLAIN.md")
def test_an_older_rb_reads_the_record_beside_it_unchanged(tmp_path):
    rec = record(tmp_path, "older")

    def older(*argv):
        r = subprocess.run([os.environ["RB_OLD_BIN"], *argv, "--json"], cwd=rec.root, capture_output=True, text=True, env=rec.env)
        out = json.loads(r.stdout)
        return r.returncode, out

    def same(d):
        # what an older rb says of the record, its clock and its look at the repository left out
        d = json.loads(json.dumps(d))
        for k in ("rb_delta",):
            d.pop(k, None)
        data = d.get("data", {})
        for k in ("scope", "repository", "found", "journal", "witness", "uncited_changes", "record_start"):
            data.pop(k, None)
        return json.dumps(d, sort_keys=True)

    without = {c: older(*c.split()) for c in ("status", "doctor", "show c1", "show e1", "compare e1")}
    with_plain(rec)
    for c, (rc, out) in without.items():
        rc2, out2 = older(*c.split())
        assert rc2 == rc, (c, out2.get("errors"))
        assert same(out2) == same(out), c
    # and it writes to the record beside it as before
    rc, out = older("note", "add", "a note beside PLAIN.md", "-e", "e1")
    assert rc == 0 and out["ok"]
    assert rec.rb("status")["overview"]["plain"]["lines"]
