"""The Overview's view model (UX5 1.1): one value, `overview`, that rb status --json, rb open's /state.json and the document
a page is drawn from all hold the same, built once from rb status's data. Each chart row reads its check's finding: its
state is the rule's outcome and its number the finding's own, and the strip counts every check linked to the question."""
import json
import os
import urllib.request

import pytest

from rbtest import Open

def overview_of(rec) -> dict:
    return rec.rb("status")["overview"]


def page_overview(rec, tmp) -> dict:
    dump = tmp / "page.view.json"
    rec.env = {**rec.env, "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    try:
        rec.rb("page", "--out", str(tmp / "page.html"))
    finally:
        rec.env = {k: v for k, v in rec.env.items() if not k.startswith("RB_TEST_SHELL")}
    return json.loads(dump.read_text(encoding="utf-8"))["overview"]


def open_overview(rec) -> dict:
    old = os.getcwd()
    saved = dict(os.environ)
    os.environ.update(rec.env)
    try:
        srv = Open(rec.root)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(srv.url + "/state.json", timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))["home"]["overview"]
        finally:
            srv.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        os.chdir(old)


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_every_surface_holds_the_same_overview(which, request, tmp_path):
    rec = request.getfixturevalue(which)
    ov = overview_of(rec)
    assert ov["question"] and ov["strip"]["groups"]
    assert page_overview(rec, tmp_path) == ov
    assert open_overview(rec) == ov


@pytest.mark.parametrize("which", ["int8_demo", "scale_record"])
def test_each_row_reads_its_checks_finding(which, request):
    rec = request.getfixturevalue(which)
    st = rec.rb("status")
    ov = st["overview"]
    findings = {f["criterion"]: f for f in st["findings"]}
    # the question's checks: its own direct links and every sub-question's, below it at any depth (R21)
    live = [q for q in st["questions"] if not q.get("retracted")]
    tree, grew = {ov["question"]["id"]}, True
    while grew:
        more = {q["id"] for q in live if q.get("parent") in tree} - tree
        tree, grew = tree | more, bool(more)
    direct = {x["criterion"] for q in live if q["id"] in tree for x in q["inquiry"]["direct"] if x["criterion"] in findings}
    in_strip = [c["id"] for g in ov["strip"]["groups"] for c in g["checks"]]
    assert sorted(in_strip) == sorted(direct)
    for g in ov["strip"]["groups"]:
        assert g["n"] == len(g["checks"])
        assert all(findings[c["id"]]["rule_outcome"] == g["state"] for c in g["checks"])
    assert ov["chart"]
    for row in ov["chart"]:
        f = findings[row["id"]]
        assert row["state"] == f["rule_outcome"] != "untested"
        assert row["est"] == f["comparison"]["estimate"]
        assert (row["lo"], row["hi"]) == (f["comparison"]["lo"], f["comparison"]["hi"])
        assert row["late"] == (f["timing"] in ("after_evidence", "after_exposure"))
        assert row["said"].startswith(f["says"]["comparison"])
    # the decisions are the calls rb status lists under Needs judgment, then what waits for a confirmation
    judged = [o["subject"] for o in st["open"] if o["who"] == "human" and o["code"] not in ("call_proposed", "past_call_unconfirmed")]
    assert [d["subject"] for d in ov["decisions"] if d["kind"] == "decide"] == judged
    assert len([d for d in ov["decisions"] if d["kind"] == "confirm" and d["code"] != "design_pending"]) == len(st["pending"])


def test_a_record_with_no_question_has_no_story(tmp_path):
    from rbtest import Recorder
    rec = Recorder(tmp_path, "empty")
    rec.start()
    rec.rb("init", "INT8 ResNet-50")
    ov = overview_of(rec)
    assert ov["question"] is None and ov["chart"] == [] and ov["decisions"] == [] and ov["strip"]["groups"] == []
