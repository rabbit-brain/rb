"""Every surface leads with where each question stands, computed as sentences from the
record; what waits on the user sits beside the work, never at the top of a page. Scoped supersession
lets an issue touch one measurement without poisoning the rest, and population.* declarations ride
along counts without ever blocking anything. The sentences are composed from stored fields; statements
and reasons inside them are verbatim, never a paraphrase of what anyone said."""
import html as html_mod
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, home, main, object_card, state

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="the research state is played in a git repository")


@pytest.fixture
def rb(workdir, monkeypatch, tmp_path_factory, capsys):
    as_the_user(monkeypatch)
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.email", "ana@example.com"], check=True)
    subprocess.run(["git", "config", "user.name", "ana"], check=True)

    def run(*argv):
        code = main([*argv, "--json"])
        return code, Envelope.model_validate_json(capsys.readouterr().out)

    return run


def prefetch(rb):
    """The prefetch shape: a question, a frozen comparison, two runs, one timing supersession."""
    assert rb("init", "Which prefetch policy is worth keeping?")[0] == 0
    assert rb("question", "add", "Can two prefetched batches keep the benefit of six?", "--id", "q1")[0] == 0
    assert rb("experiment", "add", "two-batch prefetch", "--id", "pf", "--question", "q1",
              "--hypothesis", "an adaptive rule fetches the batch needed next",
              "--baseline", "static2", "--candidate", "adaptive2", "--varies", "rule")[0] == 0
    assert rb("metric", "add", "io_calls", "--minimize", "--unit", "calls")[0] == 0
    assert rb("metric", "add", "wall_s", "--minimize", "--unit", "s")[0] == 0
    assert rb("spec", "set", "pf", "population.datasets", "2", "--no-verify")[0] == 0
    assert rb("spec", "set", "pf", "population.shards", "8", "--no-verify")[0] == 0
    assert rb("spec", "set", "pf", "static2.rule", "static", "--no-verify")[0] == 0
    assert rb("spec", "set", "pf", "adaptive2.rule", "adaptive", "--no-verify")[0] == 0
    assert rb("criterion", "add", "the adaptive rule needs fewer reads", "-e", "pf",
              "--metric", "change.io_calls", "--at-most", "-5", "--id", "cq", "-q", "q1")[0] == 0
    assert rb("criterion", "add", "runtime does not regress", "-e", "pf",
              "--metric", "change.wall_s", "--at-most", "0", "--id", "ct", "-q", "q1")[0] == 0
    assert rb("freeze", "pf", "-m", "before the counted runs")[0] == 0
    code, env = rb("run", "add", "pf", "static2.io_calls=114.6", "adaptive2.io_calls=104.7",
                   "static2.wall_s=2.41", "adaptive2.wall_s=2.17")
    assert code == 0
    first = env.data["object"]["id"]
    assert rb("run", "add", "pf", "static2.io_calls=113.9", "adaptive2.io_calls=104.1",
              "static2.wall_s=2.44", "adaptive2.wall_s=2.19")[0] == 0
    return first


# ---------------------------------------------------------------- the brief


def test_the_brief_says_where_each_question_stands_in_sentences(rb):
    prefetch(rb)
    briefs = home().briefs
    assert [b.id for b in briefs] == ["q1"]
    b = briefs[0]
    assert b.text == "Can two prefetched batches keep the benefit of six?"
    eb = b.experiments[0]
    cq = next(f for f in eb.findings if f.criterion_id == "cq")
    # Composed from stored fields: the statement verbatim, the reading with its computed count, the rule verbatim and
    # whether the reading meets it.
    assert cq.sentence.startswith("the adaptive rule needs fewer reads.")
    assert "change.io_calls: -9.85 calls · paired difference, 2 runs (rb)" in cq.sentence
    assert "estimate meets criterion: estimate -9.85 ≤ -5" in cq.sentence and cq.rule == "change.io_calls ≤ -5"
    assert "rule estimate meets criterion" not in cq.sentence
    # The declared counting structure rides along the counts, in declaration order.
    assert eb.scope == "datasets = 2 · shards = 8"
    assert eb.table is not None and eb.counted == 2
    # What changed is dated, names no writer, and quotes reasons rather than rewording them.
    assert any("criteria locked: before the counted runs" in line for line in b.changed)
    # A question is not listed as open inside its own section. rb status lists an open question an agent can act on
    # (one with nothing filed under it yet); its own section gives the command, not "q2 is open".
    assert not any("q1 is open" in line for line in b.still_open)
    assert rb("question", "add", "Does a third batch help?", "--id", "q2")[0] == 0
    q2 = next(x for x in home().briefs if x.id == "q2")
    assert any(i["subject"] == "q2" and i["code"] == "open_question" for i in rb("status")[1].data["open"])
    assert not any("q2 is open" in line for line in q2.still_open)
    assert any(c.startswith('rb experiment add "<what would test it>" -q q2') for c in q2.next)


def test_a_met_rule_reads_with_what_it_rests_on(rb):
    prefetch(rb)
    # The user vouches for the variant settings (population.* needs no vouching), and the calls sit beside the work.
    for name in ("static2.rule", "adaptive2.rule"):
        assert rb("decide", f"pf/{name}", "accept", "-m", "read off the run configs")[0] == 0
    b = home().briefs[0]
    cq = next(f for eb in b.experiments for f in eb.findings if f.criterion_id == "cq")
    # A rule met on a point only, with nothing wrong under it, is met on its estimate alone, so it never reads good; the
    # sentence ends on when the rule was declared, against the evidence it reads. No single word stands for the whole.
    assert not cq.good and cq.standing == "estimate meets criterion · point only" and "estimate meets criterion" in cq.sentence
    assert cq.sentence.rstrip().endswith("declared before the evidence (by record times).")
    assert 'accepted: "pf/static2.rule" (read off the run configs)' in b.ruled and b.still_open == []
    vouched = [c.code for c in state().finding("cq").caveats if c.subject.endswith(".rule")]
    assert vouched == ["vouched", "vouched"]


def test_population_declarations_never_block_and_are_not_nags(rb):
    prefetch(rb)
    code, env = rb("status")
    assert code == 0
    # No open item nags about population.*, and no finding reads a population declaration as a problem or a note.
    assert not any("population." in i["subject"] for i in env.data["open"])
    for f in env.data["findings"]:
        assert not any("population" in c["subject"] or "population" in c["text"] for c in f["integrity"] + f["caveats"])


# ---------------------------------------------------------------- scoped supersession


def test_a_scoped_retraction_supersedes_one_measurement_and_keeps_the_rest(rb):
    first = prefetch(rb)
    code, env = rb("retract", first, "-m", "runner lifetime bug corrupted the timing environment", "--scope", "wall_s")
    assert code == 0
    assert env.data["object"]["retracted"] == {"why": "runner lifetime bug corrupted the timing environment", "scope": ["wall_s"]}
    fq = state().finding("cq")
    ft = state().finding("ct")
    assert fq.n == 2 and fq.rule_outcome == "meets"         # the quality criterion keeps both runs
    assert ft.n == 1 and ft.rule_outcome == "meets"         # the timing criterion loses the superseded one
    assert any(c.code == "superseded_elsewhere" and "still counts" in c.text for c in fq.caveats)
    assert any(c.code == "superseded" and "runner lifetime bug" in c.text for c in ft.caveats)
    # The timing reading before the supersession stays on record as its history; the quality reading has none.
    assert fq.history == [] and "superseded for wall_s: runner lifetime bug" in ft.says["history"]
    # compare drops the superseded measurement from that run and keeps the other.
    cmp_ = state().experiment("pf").compare()
    cells = {r["variant"]: r["metrics"] for r in cmp_["rows"]}
    assert cells["adaptive2"]["io_calls"]["n"] == 2
    assert cells["adaptive2"]["wall_s"]["n"] == 1
    # status still lists the run, saying what it is superseded for.
    code, env = rb("status")
    row = next(x for x in env.data["runs"] if x["id"] == first)
    assert row["superseded_for"] == ["wall_s"]


def test_scope_is_for_runs_only_and_full_retraction_is_unchanged(rb):
    prefetch(rb)
    code, env = rb("retract", "cq", "-m", "x", "--scope", "wall_s")
    assert code == 2 and env.errors[0].code == "E_OBJECT_INVALID"
    code, env = rb("retract", "cq", "-m", "wrong statement")
    assert code == 0
    f = state().finding("cq")
    # A retracted criterion stops counting: no reading, its rule untested, and it says it was retracted.
    assert f.comparison is None and f.rule_outcome == "untested" and f.says["headline"] == "retracted"
    assert [c.code for c in f.integrity] == ["retracted"] and "wrong statement" in f.integrity[0].text


# ---------------------------------------------------------------- the surfaces lead with the state


def test_status_text_leads_with_the_question_before_the_lists(rb, capsys):
    prefetch(rb)
    # An open question with nothing filed under it is what an agent can do next (q1 has its experiment, so it is
    # not listed), and a criterion under investigation waits on the user's next call, which puts both lists on
    # the page.
    assert rb("question", "add", "Does a third batch help?", "--id", "q2")[0] == 0
    assert rb("decide", "ct", "investigate", "-m", "the timing runs share one machine")[0] == 0
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    q = out.index("Can two prefetched batches keep the benefit of six? (open)")
    assert q < out.index("estimate meets criterion: estimate -9.85 ≤ -5") < out.index("Needs judgment") < out.index("Can be done now")
    assert "scope, declared: datasets = 2" in out


def test_the_page_opens_on_the_question_and_waits_on_you_near_the_end(rb, capsys, tmp_path):
    prefetch(rb)
    out_file = tmp_path / "p.html"
    assert main(["page", "--out", str(out_file), "--json"]) == 0
    capsys.readouterr()
    text = out_file.read_text(encoding="utf-8")
    plain = re.sub(r"<[^>]+>", "", text)
    # The overview leads with the question. Evidence and full matrices are earned in their own views.
    sec = lambda page, name, after: page.split(f'<div class="sec" id="{name}">', 1)[1].split(f'<div class="sec" id="{after}">', 1)[0]
    overview, research, experiments = sec(text, "sec-overview", "sec-research"), sec(text, "sec-research", "sec-experiments"), sec(text, "sec-experiments", "record")
    assert 'href="#q-q1"' in overview and "Can two prefetched batches keep the benefit of six?" in overview
    assert 'class="igroup' not in overview and '<table' not in overview
    assert 'id="q-q1"' in research and 'id="q-q1:evidence"' in research
    assert '<table class="grid">' in experiments and 'id="h-experiments"' in experiments
    # Population beside the counts, supersession language nowhere (nothing superseded yet).
    assert "declared: datasets = 2 · shards = 8" in plain
    # The honesty line about decisions appears exactly once, in the footer.
    assert plain.count("A decision records a call; it does not change a reading.") == 1
    assert text.index("A decision records a call") > text.index("<footer class=\"foot\">")
    assert "\u2014" not in text
    # An agent's call lands as the agent's, proposed; once the user confirms it, it reads under its question. It steers
    # the line of work (continue keeps it open), so the page still asks for the next measurement.
    os.environ["CLAUDECODE"] = "1"
    try:
        assert main(["decide", "pf", "continue", "-m", "both runs show it", "--json"]) == 0
        proposed = json.loads(capsys.readouterr().out)["data"]["object"]["id"]
    finally:
        os.environ.pop("CLAUDECODE", None)
    assert main(["confirm", proposed]) == 0
    capsys.readouterr()
    # A third run where the runtime regresses leaves a rule missed: a call, never a run rb asks for.
    assert rb("run", "add", "pf", "static2.io_calls=114.2", "adaptive2.io_calls=104.5",
              "static2.wall_s=2.40", "adaptive2.wall_s=3.10")[0] == 0
    # A call is announced on the overview and its details stay in a closed disclosure: here, a run
    # nobody recorded, which rb found in the repository.
    Path("out").mkdir()
    Path("out/fourth.json").write_text('{"io_calls": 104.9}', encoding="utf-8")
    state().scan()
    assert main(["page", "--out", str(out_file), "--json"]) == 0
    capsys.readouterr()
    text2 = out_file.read_text(encoding="utf-8")
    overview2 = sec(text2, "sec-overview", "sec-research")
    assert 'id="queue"' in overview2 and re.search(r'<a class="qbtn" href="#call-[^"]+"[^>]*>Review</a>', overview2)
    # Your call is a view of its own, never a drawer the overview opens
    assert re.search(r'<section class="view" id="your-call"', text2) and not re.search(r'<details\b[^>]*\bid="your-call"', text2)
    assert "Record fourth.json on pf" in text2 and "rb approve" not in text2
    # The reading surface never teaches the commands that write the work itself (a run, a freeze, an experiment, a
    # setting), and ids stay out of the visible text. A Next step is rb's first run suggestion in words (SHELL-SPEC
    # 15, item 16): rb suggests no run for q1 now (the missed rule waits on a call), so its rail asks for none, and the
    # experiment's Reproduce asks in words for one more measurement of what its criteria read.
    plain2 = re.sub(r"<[^>]+>", " ", text2)
    question = html_mod.unescape(re.sub(r"<[^>]+>", "", text2.split('id="q-q1"', 1)[1].split("<article", 1)[0]))
    # the call's line first, what it is on trailing (design pass 1, P3)
    assert re.search(r"continued \d{4}-\d{2}-\d{2} · note: both runs show it ?pf", question), question
    for cmd in ("rb run add", "rb freeze", "rb experiment add", "rb spec set"):
        assert cmd not in plain2
    assert main(["status", "--json"]) in (0, 1)
    assert not [i for i in json.loads(capsys.readouterr().out)["data"]["open"] if i["do"].startswith("rb run add ")]
    rail = text2.split('id="q-q1:rail"', 1)[1].split("</section>", 1)[0]
    assert "Next step" not in rail and "run one more" not in rail
    reproduce = html_mod.unescape(re.sub(r"<[^>]+>", " ", text2.split('id="x-pf:reproduce"', 1)[1].split("</section>", 1)[0]))
    assert re.search(r'Next step\s+"run one more io_calls measurement on two-batch prefetch, adaptive2 vs static2, and record it in rb"', reproduce), reproduce
    assert "criterion" in plain2 and "margin" in plain2


def test_the_question_card_carries_the_brief(rb):
    prefetch(rb)
    c = object_card("q1")
    # The same scoped reading as every other question surface: verbatim statement, comparison, rule and qualifications.
    assert c.sentences == home().briefs[0].summary
    reading = next(s for s in c.sentences if s.startswith("the adaptive rule needs fewer reads."))
    assert "change.io_calls: -9.85 calls" in reading and "estimate meets criterion: estimate -9.85 ≤ -5" in reading
    assert "2 runs" in reading and "point only" in reading
    assert any("declared population" in f for f in c.facts)
    # Provenance and qualifications remain readable, while action commands belong to the agent prompt.
    assert not any(command in s for s in c.sentences for command in ("rb run add", "rb criterion file", "rb freeze", "rb spec set"))


def test_a_runs_receipt_opens_from_the_experiment_card(rb):
    """TUI parity for the receipt on demand: the experiment's card lists its runs as openable lines
    (no ids in the words), and a run's own card is the receipt: stored fields verbatim, labels the
    fields' own names, the retraction in the record's words."""
    first = prefetch(rb)
    c = object_card("pf")
    assert c.runs and all(r["label"] and r["id"] for r in c.runs)
    assert not any(r["id"] in r["label"] for r in c.runs)
    r = object_card(first)
    assert r.title == "two-batch prefetch" and "io_calls=114.6" in r.lead
    assert r.who_line.startswith("run · recorded ")
    assert any(f.startswith("environment: ") and "python" in f for f in r.facts)
    assert any(f.startswith("repository when recorded: ") for f in r.facts)
    assert rb("retract", first, "-m", "runner lifetime bug", "--scope", "wall_s")[0] == 0
    r2 = object_card(first)
    assert any("superseded for wall_s" in b and "runner lifetime bug" in b for b in r2.blockers)
