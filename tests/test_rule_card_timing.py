"""MP-1111-0219's S2: a rule card on a page and on rb open says when its rule was written by the finding's timing code,
never by the start of rb's sentence. Every "order unknown" timing (a run typed in with no --produced-at, and one whose
evidence git took out a part of) read "No results yet." on 0.8.4 and on 085ffcf, since the sentence opens
"declared; order unknown: ...". One case per timing kind, on the page and on rb open."""
import html
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, Open, as_the_user, command, environment

pytestmark = pytest.mark.skipif(shutil.which("git") is None or sys.platform == "win32", reason="needs git and a POSIX shell")
GIT = shutil.which("git")

# a rule written before or after its results is placed against them in time (no glossary, show instead, Nov 12 20:35 +08)
DAY = r"\d{1,2} [A-Z][a-z]{2}"
PLACED = {
    "before": re.compile(rf"Rule {DAY} \u2500\u2500\u25b6 results {DAY}\."),
    "after_evidence": re.compile(rf"Results {DAY} \u2500\u2500\u25b6 rule {DAY}\."),
}
SAID = {
    "before": "Written before its results.",
    "after_evidence": "Written after its results came in.",
    "after_exposure": "Written after the data was seen.",
    "unknown": "rb can't tell whether it was written before its results.",
    "untested": "No results yet.",
    "taken_out": "Its results were taken out of the record by git.",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    as_the_user(monkeypatch)
    for k in AGENT_VARS:
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\temail = ana@example.org\n\tname = ana\n[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    return tmp_path


def rb(root: Path, *argv: str) -> dict:
    r = subprocess.run([*command(), *argv, "--json"], cwd=root, env=environment(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode in (0, 1), (argv, r.stdout, r.stderr)
    return json.loads(r.stdout)


def git(root: Path, *argv: str) -> None:
    subprocess.run([GIT, *argv], cwd=root, check=True, capture_output=True)


def text_of(page: str) -> str:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def build(root: Path, kind: str) -> None:
    root.mkdir()
    git(root, "init", "-q")
    for argv in (("init", "INT8 ResNet-50", "--no-agents"), ("experiment", "add", "INT8 against FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8"),
                 ("spec", "set", "int8", "seed", "--per-run"), ("metric", "add", "top1", "--maximize"),
                 ("resource", "add", "holdout", "--type", "dataset", "--name", "held-out images", "--url", "https://data.example.org/holdout")):
        rb(root, *argv)
    seeds = lambda at: [rb(root, "run", "add", "int8", f"int8.top1=75.{n}", f"fp32.top1=76.{n}", "--set", f"seed={n}", "--input", "holdout:eval", *at(n)) for n in (1, 2, 3)]
    rule = ("criterion", "add", "INT8 loses at most one point", "-e", "int8", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    if kind == "after_evidence":
        seeds(lambda n: ("--produced-at", f"2026-01-0{n}T10:00:00Z"))
        rb(root, *rule)
    elif kind == "after_exposure":
        rb(root, "resource", "expose", "holdout", "inspected", "--at", "2026-01-01", "--why", "looked at cases")
        rb(root, *rule)
        seeds(lambda n: ("--produced-at", "2099-01-01T10:00:00Z"))
    else:
        rb(root, *rule)
        if kind == "before":
            (root / "out").mkdir()
            for n in (1, 2, 3):
                f = root / "out" / f"seed{n}.json"
                f.write_text(json.dumps({"int8": {"top1": 75.0 + n / 10}, "fp32": {"top1": 76.0 + n / 10}}), encoding="utf-8")
                rb(root, "run", "add", "int8", "--from", f"out/{f.name}", "--set", f"seed={n}")
        elif kind == "unknown":
            seeds(lambda n: ())          # typed in, with no --produced-at: when they were made is unknown (chk0219/t.sh)
        elif kind == "taken_out":
            git(root, "add", "-A")
            git(root, "commit", "-qm", "the rule")
            seeds(lambda n: ("--produced-at", "2099-01-01T10:00:00Z"))
            git(root, "checkout", "--", ".")


@pytest.mark.parametrize("kind", sorted(SAID))
def test_the_rule_card_says_when_its_rule_was_written_by_its_timing_code(home, kind):
    root = home / kind
    build(root, kind)
    f = rb(root, "show", "c1")["data"]["finding"]
    code = "untested" if kind == "taken_out" else kind
    assert f["timing"] == code, (kind, f["timing"], f["says"]["timing"])
    p = home / f"{kind}.html"
    subprocess.run([*command(), "page", "--out", str(p)], cwd=root, env=environment(), check=True, capture_output=True)
    server = Open(root)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(server.url + "/", timeout=60) as resp:
            live = resp.read().decode("utf-8")
    finally:
        server.close()
    for where, page in (("the page", p.read_text(encoding="utf-8")), ("rb open", live)):
        said = text_of(page)
        assert PLACED[kind].search(said) if kind in PLACED else SAID[kind] in said, (kind, where)
        for other, words in SAID.items():
            if other != kind and other != "untested":
                assert words not in said, (kind, where, words)
        if kind != "untested":
            assert "No results yet." not in said, (kind, where)
