"""One reading, one set of numbers: where a paired reading leaves out a run, the unpaired reading beside it reads all the
arms' runs (never a second reading of the paired runs alone), and is left out where it says what the paired one says; a
note's number keeps the digits the rule's tolerance needs; the page says a tolerance as the amount it is, names a metric
by its declared name, and gives its grids and tables the digits the rules give. Every name and number is synthetic: an
INT8 ResNet-50 on ImageNet, recorded by human:ana."""
import html
import re
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, main


@pytest.fixture
def rb(tmp_path, monkeypatch, capsys):
    as_the_user(monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    root = tmp_path / "int8"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    monkeypatch.chdir(root)

    def run(*argv, ok=True, text=False):
        if text:
            code = main(list(argv), typed=True)
            return code, capsys.readouterr().out
        code = main([*argv, "--json"], typed=True)
        env = Envelope.model_validate_json(capsys.readouterr().out)
        if ok:
            assert code in (0, 1) and env.ok, (argv, code, env.errors)
        return env

    run("init", "INT8 ResNet-50", "--no-agents")
    run("metric", "add", "top1", "--maximize", "--unit", "percent")
    return run


def page_text(rb):
    rb("page", "--out", "page.html")
    return html.unescape(re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8")))


def lone_seed(rb):
    """INT8 on 3 seeds, FP32 on 2: the paired reading leaves the third INT8 run out."""
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e1", "seed", "--per-run")
    rb("spec", "set", "e1", "population.unit", "seed", "--no-verify")
    for seed, x in [(1, 75.10), (2, 75.30)]:
        rb("run", "add", "e1", f"fp32.top1={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    for seed, x in [(1, 75.60), (2, 75.70), (3, 78.00)]:
        rb("run", "add", "e1", f"int8.top1={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")


def test_the_unpaired_reading_reads_every_run_of_the_arms(rb):
    lone_seed(rb)
    rb("criterion", "add", "INT8 gains", "-e", "e1", "--metric", "change.top1", "--at-least", "0", "--id", "c1")
    f = rb("show", "c1").data["finding"]
    assert f["comparison"]["n"] == 2 and abs(f["comparison"]["estimate"] - 0.45) < 1e-9
    unpaired = f["comparison"]["pairing"]["unpaired"]
    # the arms' means over all their runs: (75.6 + 75.7 + 78) / 3 - (75.1 + 75.3) / 2 = 1.2333
    assert unpaired["n"] == 5 and abs(unpaired["estimate"] - (229.3 / 3 - 75.2)) < 1e-9, unpaired
    said = f["comparison"]["pairing"]["said"]
    assert "unpaired, all the arms' runs, those it leaves out too, read +1.233" in said, said
    assert said.count("unpaired, ") == 1


def test_an_unpaired_reading_the_same_as_the_paired_one_is_left_out(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e2", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e2", "seed", "--per-run")
    rb("spec", "set", "e2", "population.unit", "seed", "--no-verify")
    rb("criterion", "add", "INT8 gains", "-e", "e2", "--metric", "change.top1", "--at-least", "0", "--id", "c2")
    rb("run", "add", "e2", "fp32.top1=75.1", "--set", "seed=1", "--produced-at", "2026-05-01")
    rb("run", "add", "e2", "int8.top1=75.6", "--set", "seed=1", "--produced-at", "2026-05-01")
    said = rb("show", "c2").data["finding"]["comparison"]["pairing"]["said"]
    assert "paired by seed, the declared unit" in said and "unpaired" not in said, said


def test_a_note_s_number_keeps_the_digits_the_rule_s_tolerance_needs(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e5", "--purpose", "the sweep", "--baseline", "fp32", "--candidate", "int8")
    rb("spec", "set", "e5", "seed", "--per-run")
    rb("spec", "set", "e5", "population.unit", "seed", "--no-verify")
    for seed, x in [(1, 64.55), (2, 64.56)]:
        rb("run", "add", "e5", f"fp32.top1={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    for seed, x in [(1, 66.44), (2, 66.44), (3, 66.4394)]:
        rb("run", "add", "e5", f"int8.top1={x}", "--set", f"seed={seed}", "--produced-at", "2026-05-01")
    Path("README.md").write_text("INT8 against FP32: +1.89 points.\n", encoding="utf-8")
    rb("criterion", "add", "The README's difference", "-e", "e5", "--metric", "change.top1", "--equals", "1.89", "--tolerance", "0.005",
       "--source", "README.md", "--quote", "+1.89", "--id", "c3")
    notes = [c["text"] for c in rb("show", "c3").data["finding"]["caveats"] if c["code"] == "cited_differs"]
    # the arms' means differ by 66.4398 - 64.555 = 1.8848, outside 1.89 ± 0.005: never said as +1.885, on the edge
    assert notes and "which is +1.8848 here" in notes[0], notes


def test_the_page_says_a_tolerance_of_a_percentage_in_points(rb):
    rb("experiment", "add", "INT8 alone", "--id", "e3", "--purpose", "the level", "--baseline", "int8")
    Path("README.md").write_text("INT8 scores 75.6% top-1.\n", encoding="utf-8")
    rb("criterion", "add", "The README's top-1", "-e", "e3", "--metric", "top1", "--equals", "75.6", "--tolerance", "0.05", "--source", "README.md", "--quote", "75.6%", "--id", "c4")
    rb("run", "add", "e3", "top1=75.62", "--produced-at", "2026-05-01")
    text = page_text(rb)
    assert "within 0.05 points of 75.6%" in text, re.findall(r"within [^.]{0,40}", text)
    assert "within 0.05% of" not in text


def test_the_page_names_a_metric_as_declared_and_gives_its_grid_the_rule_s_digits(rb):
    rb("metric", "add", "val_bpb", "--minimize", "--unit", "bpb")
    rb("experiment", "add", "INT8 against FP32", "--id", "e4", "--purpose", "the loss", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 is 0.329 bpb below FP32", "-e", "e4", "--metric", "change.val_bpb", "--equals", "-0.329", "--tolerance", "0.0005", "--id", "c5")
    rb("run", "add", "e4", "fp32.val_bpb=1.463472", "int8.val_bpb=1.134565", "--produced-at", "2026-05-01")
    text = page_text(rb)
    assert "Val (bpb)" not in text and "on Bpb" not in text, re.findall(r".{40}(?:Val \(bpb\)|on Bpb)", text)
    assert "val_bpb" in text
    # the grid's means in the rule's digits (it reads -0.3289): never 1.46 and 1.13
    assert "1.4635" in text and "1.1346" in text, re.findall(r"1\.[0-9]+", text)


def test_the_page_names_every_metric_as_declared_and_its_runs_in_rb_s_digits(rb):
    """acc heads "acc", m.acc "m.acc", never "Acc" or "M acc"; a run's card and its runs table row give rb show's
    digits (49.69), never the stored float (round 5)."""
    rb("metric", "add", "acc", "--maximize")
    rb("metric", "add", "m.acc", "--maximize")
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 keeps m.acc", "-e", "e1", "--metric", "change.m.acc", "--at-least", "-1", "--id", "c1")
    rb("criterion", "add", "INT8 keeps acc", "-e", "e1", "--metric", "change.acc", "--at-least", "-1", "--id", "c2")
    rid = rb("run", "add", "e1", "fp32.acc=49.59", "int8.acc=49.690000000000005", "fp32.m.acc=0.5", "int8.m.acc=0.51", "--produced-at", "2026-05-01").data["object"]["id"]
    rb("page", "--out", "page.html")
    raw = Path("page.html").read_text(encoding="utf-8")
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    assert "49.690000000000005" not in raw and "49.69" in text, re.findall(r"49\.69\d*", raw)
    assert "M acc" not in text and "Acc," not in text and " Acc " not in text, re.findall(r".{20}\b[AM] ?acc.{20}", text)
    assert "<dt>m.acc, INT8</dt><dd>0.51</dd>" in raw and "<dt>acc, INT8</dt><dd>49.69</dd>" in raw, raw[raw.find(f"run-{rid}:results"):][:400]
    # closed form (owner, Nov 13 2026): a table's head opens what it heads (the runs, or the rules), so it may be a link
    for name in ("m.acc", "acc"):
        assert re.search(rf'<th>(?:<a href="#[^"]+">)?{re.escape(name)}(?:</a>)?</th>', raw), name


def test_the_page_s_experiment_rows_count_rules_met_on_the_estimate_alone(rb):
    rb("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "the table", "--baseline", "fp32", "--candidate", "int8")
    rb("criterion", "add", "INT8 loses at most a point", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    rb("run", "add", "e1", "fp32.top1=76.1", "int8.top1=75.6", "--produced-at", "2026-05-01")
    rb("page", "--out", "page.html")
    raw = Path("page.html").read_text(encoding="utf-8")
    rows = raw[raw.find('id="experiments"'):]
    row = html.unescape(re.sub(r"<[^>]+>", "", rows[:rows.find("</li>")]))
    # closed form (owner, Nov 13 2026): a page says a rule's state as "holds"
    assert "Rules: 1 rule holds, on the estimate alone" in row, row


def test_a_rule_s_tolerance_reads_in_digits_on_the_terminal_and_the_page(rb):
    rb("experiment", "add", "INT8 alone", "--id", "e1", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 matches the stated 0.999349", "-e", "e1", "--metric", "top1", "--equals", "0.999349", "--tolerance", "0.0000005", "--id", "c1")
    rb("run", "add", "e1", "top1=0.9993492", "--produced-at", "2026-05-01")
    code, out = rb("status", "--full", text=True)
    assert "± 0.0000005" in out and "5e-07" not in out, out
    text = page_text(rb)
    assert "5e-07" not in text and "± 0.0000005" in text


def test_a_run_s_number_keeps_every_digit_it_was_given_on_rb_show_and_the_page(rb):
    """15 significant digits: float noise left out, never a digit given cut (1234.5679, never 1234.57)."""
    rb("experiment", "add", "INT8 alone", "--id", "e1", "--purpose", "a level", "--baseline", "int8")
    rb("criterion", "add", "INT8 holds 1234.5678", "-e", "e1", "--metric", "top1", "--at-least", "1234.5678", "--id", "c1")
    rid = rb("run", "add", "e1", "top1=1234.5679", "big=123456789.123", "--produced-at", "2026-05-01").data["object"]["id"]
    code, out = rb("show", rid, text=True)
    assert "int8.top1 = 1234.5679" in out and "int8.big = 123456789.123" in out, out
    rb("page", "--out", "page.html")
    raw = Path("page.html").read_text(encoding="utf-8")
    assert "1234.5679" in raw and "1234.57<" not in raw and "1.23457e+08" not in raw and "123456789.123" in raw
