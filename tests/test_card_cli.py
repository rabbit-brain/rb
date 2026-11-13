"""The card (UX5 2.1) and every citation the CLI prints, opened (UX5 2.2, section 4).

- `rb show <id>` draws a check, a run, a setting, a decision and an experiment as fields, wrapped to the terminal: each
  field's label in `card.fields` order, each value and item `rb show --json` holds, and never a paragraph of rb's.
  `--long` prints the long form; a file, a file's lines or key, a file at a commit, a folder and a commit each have a
  card too.
- The citation walk: every id, path and commit that `rb status`, `rb status --full`, `rb show` and `rb files` print is
  opened by the one command the CLI gives for it, and every `card.cites[].opens` resolves. A list that ends in `+n
  more` names the command that shows the rest.

The records: the INT8 demo and the scale record (tests/rbtest.py), and, where RB_SAMPLE_RECORDS names them (folders
separated by the path separator), copies of the hosted samples' records (int8-cifar and math-prompts)."""
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from rbtest import command

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="every record here is a git repository")

LABELS = {"check": ["CHECK", "KIND", "", "RESULT", "PASSES", "CITED", "SEEDS", "UNITS", "WRITTEN", "RUNS", "SETTINGS", "CODE", "PROBLEMS", "DECISION", "OPEN"],
          "run": ["RUN", "PER RUN", "NUMBERS", "WHEN", "FILES", "CODE", "COUNTS IN", "READ", "MADE", "COMMAND", "OPEN"],
          "setting": ["SETTING", "SOURCE", "READ AT", "CHECKS", "USED BY", "OPEN"],
          "decision": ["DECISION", "STATE", "WHY", "WORDS", "WHEN", "REVISIT", "SOURCE", "MADE ON", "CONFIRM", "OPEN"],
          "experiment": ["EXPERIMENT", "", "EXPECTS", "PURPOSE", "VARIANTS", "VARIES", "CHECKS", "SETTINGS", "RUNS", "NEXT", "PLAN", "OPEN"],
          "file": ["FILE", "CITED BY", "KEY", "LINES", "OPEN"],
          "folder": ["FOLDER", "IN IT", "MARKS"],
          "commit": ["COMMIT", "COMMITTED", "CODE OF", "YOUR CHECKOUT", "OPEN"]}


def samples() -> list:
    return [Path(p) for p in os.environ.get("RB_SAMPLE_RECORDS", "").split(os.pathsep) if p]


class Sample:
    """A copy of a sample's record, run as the user."""

    def __init__(self, src: Path, tmp: Path, like) -> None:
        self.root = tmp / src.name
        shutil.copytree(src, self.root, symlinks=True)
        self.env = dict(like.env)


@pytest.fixture(params=["int8_demo", "scale_record", *[f"sample:{p}" for p in samples()]])
def rec(request, tmp_path):
    if request.param.startswith("sample:"):
        return Sample(Path(request.param[7:]), tmp_path, request.getfixturevalue("int8_demo"))
    return request.getfixturevalue(request.param)


def run(rec, *argv: str, columns: int = 100, env: dict = None):
    e = {**rec.env, "COLUMNS": str(columns), "LANG": "C.UTF-8", **(env or {})}
    e.pop("NO_COLOR", None)
    return subprocess.run([*command(), *argv], cwd=rec.root, env=e, capture_output=True, encoding="utf-8", stdin=subprocess.DEVNULL)


def data(rec, *argv: str) -> dict:
    r = run(rec, *argv, "--json")
    env = json.loads(r.stdout)
    assert env["ok"], (argv, env["errors"])
    return env["data"]


def subjects(rec) -> dict:
    """One subject of each kind a card is drawn for, from the record's own data."""
    st = data(rec, "status")
    out = {}
    tested = [f for f in st["findings"] if f["rule_outcome"] != "untested"] or st["findings"]
    if tested:
        out["check"] = tested[0]["criterion"]
    if st["runs"]:
        out["run"] = st["runs"][0]["id"]
    if st["experiments"]:
        out["experiment"] = st["experiments"][0]["id"]
    made = [d for d in st["decisions"] if not d.get("retracted")]
    if made:
        out["decision"] = made[0]["id"]
    for e in st["experiments"]:
        shown = data(rec, "show", e["id"], "--long")
        settings = [s for s in shown["object"].get("settings", []) if not s.get("retracted")]
        if settings:
            out["setting"] = f"{e['id']}/{settings[0]['name']}"
            break
    head = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=rec.root, capture_output=True, text=True).stdout.strip()
    if head:
        out["commit"] = head
    readme = next((p for p in ("README.md", "notes.md", "configs/train.yaml") if (Path(rec.root) / p).is_file()), None)
    if readme:
        out["file"] = f"{readme}:1"
    folder = next((p for p in ("results/", "configs/", "out/") if (Path(rec.root) / p).is_dir()), None)
    if folder:
        out["folder"] = folder
    return out


def drawn_fields(text: str, card: dict) -> list:
    """The labels of the card's fields, in the order the text draws them."""
    seen = []
    for f in card["fields"]:
        if not f["label"]:
            continue
        if f is card["fields"][0]:
            head = f"{f['label']} {card['id']}"
            assert text.splitlines()[0].startswith(head), (text.splitlines()[0], head)
            seen.append(f["label"])
            continue
        hits = [i for i, ln in enumerate(text.splitlines()) if ln.startswith(f["label"]) and (len(ln) == len(f["label"]) or ln[len(f["label"])] == " ")]
        assert hits, (f["label"], text)
        seen.append(f["label"])
    return seen


def test_each_card_is_drawn_as_its_fields_in_the_order_the_json_holds(rec):
    for kind, subject in subjects(rec).items():
        card = data(rec, "show", subject)["card"]
        assert card["kind"] == kind, (subject, card["kind"])
        text = run(rec, "show", subject).stdout
        labels = [f["label"] for f in card["fields"] if f["label"]]
        assert labels == [x for x in LABELS[kind] if x in labels], (kind, labels)
        drawn_fields(text, card)
        # each value and each item the JSON holds is on the screen, as written (wrapped between words)
        flat = " ".join(" ".join(ln[2:] if ln.startswith("┆ ") else ln for ln in text.splitlines()).split())
        for f in card["fields"]:
            if f["key"] in ("lines",):
                continue
            if f["value"] and not f.get("figure"):
                assert " ".join(f["value"].split()) in flat, (kind, f["key"], f["value"])
            for it in f["items"]:
                assert " ".join(it["text"].split()) in flat, (kind, f["key"], it["text"])
        # never a paragraph of rb's: no line of rb's runs past the width, and no field holds two sentences of rb's
        for f in card["fields"]:
            if f["key"] not in ("statement", "title", "why", "words", "hypothesis", "purpose", "gate", "command", "lines"):
                assert f["value"].count(". ") == 0, (kind, f["key"], f["value"])


def test_the_long_form_stays_one_flag_away_and_the_json_keeps_both(rec):
    subs = subjects(rec)
    c = subs["check"]
    long = run(rec, "show", c, "--long").stdout
    assert long.startswith(f"Criterion {c} on "), long[:80]
    d = data(rec, "show", c)
    assert d["finding"]["criterion"] == c and d["card"]["kind"] == "check"
    assert d["card"]["long"] == f"rb show {c} --long"


def test_lines_wrap_to_the_terminal_and_ascii_where_the_locale_is_not_utf8(rec):
    subs = subjects(rec)
    for columns in (60, 80, 120):
        for subject in (subs["check"], subs["run"], subs["experiment"]):
            for ln in run(rec, "show", subject, columns=columns).stdout.splitlines():
                assert len(ln) <= columns, (columns, subject, ln)
    ascii = run(rec, "show", subs["check"], env={"LC_ALL": "C"}).stdout
    assert all(ord(ch) < 128 for ch in ascii if ch not in "".join(ascii_ok(rec, subs["check"]))), ascii


def ascii_ok(rec, subject) -> list:
    """Characters of someone's own text, which rb draws as written whatever the locale."""
    card = data(rec, "show", subject)["card"]
    return [ch for f in card["fields"] if f["key"] in ("statement", "title") for ch in f["value"]]


def test_words_puts_each_fields_said_under_it(rec):
    c = subjects(rec)["check"]
    card = data(rec, "show", c)["card"]
    flat = " ".join(" ".join(ln[2:] if ln.startswith("┆ ") else ln for ln in run(rec, "show", c, "--words", columns=200).stdout.splitlines()).split())
    for f in card["fields"]:
        if f["said"] and f["key"] != "open":
            assert " ".join(f["said"].split()) in flat, f["said"]


# ---------------------------------------------------------------- every citation opens

ID = re.compile(r"(?<![\w./@#-])([a-z][a-z0-9-]*[0-9a-z])(?![\w-])")
PATHLINE = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z]\w*(?::\d+(?:-\d+)?|#[\w.\[\]=-]+)?(?:@[0-9a-f]{7,40})?)(?![\w/])")
COMMIT = re.compile(r"(?<![\w/@-])([0-9a-f]{12})(?![\w-])")
COMMAND = re.compile(r"\brb (show|files)( [^\s·]+)?((?: --long)?)")


def opens(rec, cmd: str) -> bool:
    argv = shlex.split(cmd)
    assert argv[0] in ("rb", "git"), cmd
    if argv[0] == "git":
        return subprocess.run(argv, cwd=rec.root, capture_output=True).returncode == 0
    r = run(rec, *argv[1:], "--json")
    env = json.loads(r.stdout)
    return r.returncode == 0 and env["ok"]


def citations(text: str, ids: set, root: Path) -> set:
    """Each citation a text prints, as the command that opens it."""
    found = set()
    for m in COMMAND.finditer(text):
        if m.group(2) and not m.group(2).strip().startswith("<"):
            found.add(f"rb {m.group(1)}{m.group(2)}{m.group(3)}")
    for m in ID.finditer(text):
        if m.group(1) in ids:
            found.add(f"rb show {m.group(1)}")
    for m in PATHLINE.finditer(text):
        p = m.group(1)
        base = re.split(r"[:#@]", p)[0]
        if (root / base).is_file() and not base.startswith(".rb/"):
            found.add(f"rb show {shlex.quote(p)}")
    for m in COMMIT.finditer(text):
        if subprocess.run(["git", "cat-file", "-e", m.group(1) + "^{commit}"], cwd=root, capture_output=True).returncode == 0:
            found.add(f"rb show {m.group(1)}")
    return found


def test_every_citation_the_cli_prints_opens(rec):
    st = data(rec, "status")
    ids = {f["criterion"] for f in st["findings"]} | {r["id"] for r in st["runs"]} | {e["id"] for e in st["experiments"]}
    ids |= {q["id"] for q in st["questions"]} | {d["id"] for d in st["decisions"]} | {r["id"] for r in st.get("resources", [])}
    root = Path(rec.root)
    todo = []
    for argv in (("status",), ("status", "--full"), ("files",)):
        r = run(rec, *argv)
        if r.returncode == 2:
            # a command this build does not have yet (rb files, --full before round 1 lands)
            continue
        todo.extend(sorted(citations(r.stdout, ids, root)))
    seen, failed, kinds = set(), [], set()
    budget = 160
    while todo and budget:
        cmd = todo.pop(0)
        if cmd in seen:
            continue
        seen.add(cmd)
        budget -= 1
        if not opens(rec, cmd):
            failed.append(cmd)
            continue
        argv = shlex.split(cmd)
        if argv[:2] == ["rb", "show"]:
            d = data(rec, *argv[1:])
            card = d.get("card")
            if card:
                kinds.add(card["kind"])
                # what the card cites, each by the command it gives, and what its text prints
                todo.extend(c["opens"] for c in card["cites"] if c["opens"] not in seen)
                if "--long" not in argv:
                    todo.extend(sorted(citations(run(rec, *argv[1:]).stdout, ids, root) - seen))
    assert not failed, failed
    assert {"check", "run", "experiment"} <= kinds, kinds


def test_every_cites_opens_resolves_on_each_kind(rec):
    for kind, subject in subjects(rec).items():
        card = data(rec, "show", subject)["card"]
        for c in card["cites"]:
            assert c["kind"] in ("record", "setting", "file", "folder", "commit", "long", "command"), c
            assert opens(rec, c["opens"]), (kind, subject, c)


def test_a_list_that_ends_in_more_names_how_to_see_the_rest(rec):
    for kind, subject in subjects(rec).items():
        card = data(rec, "show", subject)["card"]
        for f in card["fields"]:
            for it in f["items"]:
                if re.match(r"\+\d+ more", it["text"]):
                    assert it["opens"] and opens(rec, it["opens"]), (kind, f["key"], it)


def test_a_file_a_key_a_commit_and_a_folder_open_where_the_record_cites_them(int8_demo):
    st = data(int8_demo, "status")
    root = Path(int8_demo.root)
    # a setting's source by its key: the card marks the key's line
    for e in st["experiments"]:
        for s in data(int8_demo, "show", e["id"], "--long")["object"].get("settings", []):
            src = s.get("source") or {}
            if src.get("kind") == "file" and src.get("key") and (root / src["path"]).is_file():
                card = data(int8_demo, "show", f"{src['path']}#{src['key']}")["card"]
                assert card["kind"] == "file"
                lines = next(f for f in card["fields"] if f["key"] == "lines")
                marked = [it["text"] for it in lines["items"] if it["text"].startswith("▶")]
                assert marked and src["key"].split(".")[-1] in marked[0], (src, marked)
                cited = [it["text"] for f in card["fields"] if f["key"] == "cited_by" for it in f["items"]]
                assert f"{e['id']}/{s['name']}" in cited
                break
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    path = next(p for p in tracked if not p.startswith(".rb/") and p.endswith((".md", ".yaml", ".json", ".py")))
    at = data(int8_demo, "show", f"{path}@{head[:12]}")["card"]
    assert at["kind"] == "file" and "at commit" in at["fields"][0]["value"]
    assert data(int8_demo, "show", head[:12])["card"]["kind"] == "commit"
    folder = path.rsplit("/", 1)[0] + "/" if "/" in path else None
    if folder:
        assert data(int8_demo, "show", folder)["card"]["kind"] == "folder"
    # a record file is still read as one
    rec_file = next((root / ".rb" / "questions").glob("*.yaml"))
    assert "valid" in data(int8_demo, "show", str(rec_file.relative_to(root)))


def test_a_name_that_opens_nothing_is_still_refused(int8_demo):
    for subject in ("nosuchthing", "no/such/file.yaml:3", "deadbeefdead"):
        r = run(int8_demo, "show", subject, "--json")
        assert r.returncode == 2 and json.loads(r.stdout)["errors"][0]["code"] in ("E_OBJECT_NOT_FOUND", "E_FILE_NOT_FOUND"), (subject, r.stdout)


def test_a_proposed_call_reads_as_waiting_with_its_words_as_given(int8_demo, tmp_path):
    """A call recorded in an agent session is proposed, not made: its card says it waits, gives the command that
    confirms it, and says the words given with it as given, never as the user's own."""
    root = tmp_path / "rec"
    shutil.copytree(int8_demo.root, root, symlinks=True)
    rec = Sample.__new__(Sample)
    rec.root, rec.env = root, dict(int8_demo.env)
    st = data(rec, "status")
    exp = st["experiments"][0]["id"]
    env = {**rec.env, "CLAUDECODE": "1", "RB_SESSION": "sess-card-1"}
    r = subprocess.run([*command(), "decide", exp, "park", "--why", "two seeds do not settle it", "--words", "Park it.", "--json"], cwd=root, env=env, capture_output=True, encoding="utf-8")
    did = json.loads(r.stdout)["data"]["object"]["id"]
    card = data(rec, "show", did)["card"]
    keys = [f["key"] for f in card["fields"]]
    assert "state" in keys and "confirm" in keys, keys
    state = next(f for f in card["fields"] if f["key"] == "state")["value"]
    assert "waits for a confirmation" in state
    words = next(f for f in card["fields"] if f["key"] == "words")["value"]
    assert words.startswith("words (as given): "), words


def test_rb_s_own_files_are_never_a_file_card(int8_demo):
    for subject in (".rb/log.jsonl", ".rb/log.jsonl:1", ".rb/project.yaml#title"):
        r = run(int8_demo, "show", subject, "--json")
        assert r.returncode == 2, (subject, r.stdout[:300])
        assert "actor" not in run(int8_demo, "show", subject).stdout


def test_a_line_past_the_end_says_so_and_a_long_file_names_its_next_lines(int8_demo):
    root = Path(int8_demo.root)
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    path = next(p for p in tracked if not p.startswith(".rb/") and p.endswith((".md", ".yaml", ".py")))
    n = len((root / path).read_text(encoding="utf-8").splitlines())
    card = data(int8_demo, "show", f"{path}:{n + 5}")["card"]
    lines = next(f for f in card["fields"] if f["key"] == "lines")
    assert "past the end" in lines["value"] and str(n) in lines["value"]
    assert "past the end" in run(int8_demo, "show", f"{path}:{n + 5}").stdout
    long = Path(root / "long.txt")
    long.write_text("".join(f"line {i}\n" for i in range(1, 101)), encoding="utf-8")
    try:
        card = data(int8_demo, "show", "long.txt")["card"]
        tail = next(f for f in card["fields"] if f["key"] == "lines")["items"][-1]
        assert tail["text"] == "+60 more lines, the next 40" and tail["opens"] == "rb show long.txt:41-80", tail
        text = run(int8_demo, "show", "long.txt").stdout
        assert "+60 more lines, the next 40: rb show long.txt:41-80" in text
        last = data(int8_demo, "show", "long.txt:81-100")["card"]
        assert not any(it["text"].startswith("+") for f in last["fields"] for it in f["items"])
    finally:
        long.unlink()


def test_a_tail_in_the_text_names_its_command(rec):
    for kind, subject in subjects(rec).items():
        card = data(rec, "show", subject)["card"]
        text = " ".join(run(rec, "show", subject, columns=200).stdout.split())
        for f in card["fields"]:
            for it in f["items"]:
                if re.match(r"\+\d+ more", it["text"]) and it["opens"]:
                    assert it["opens"] in text, (kind, it)


def test_rb_files_shows_the_line_each_setting_reads_and_it_opens(int8_demo):
    """A setting's source is a citation: `rb files <file>` shows the line its key or line names (at the commit the
    setting reads it at, where it names one), and the cite it prints opens with `rb show`."""
    st = data(int8_demo, "status")
    for e in st["experiments"]:
        for s in data(int8_demo, "show", "--long", e["id"])["object"].get("settings", []):
            src = s.get("source") or {}
            if src.get("kind") == "file" and (src.get("key") or src.get("line")) and (Path(int8_demo.root) / src["path"]).is_file():
                text = run(int8_demo, "files", src["path"]).stdout
                heads = [ln for ln in text.splitlines() if ln.endswith(f"({e['id']}/{s['name']})")]
                assert heads, (src, text)
                cite = heads[0].rsplit(" (", 1)[0]
                assert opens(int8_demo, f"rb show {shlex.quote(cite)}"), cite
                block = text.split(heads[0], 1)[1].split("\n\n", 1)[0]
                assert "▸" in block, block
                return
    pytest.skip("no setting with a file source by key or line here")


def test_a_check_says_its_kind_a_claim_where_it_checks_a_files_number(int8_demo):
    """The ruling of Nov 12 05:30 +08: a check of a claim a file states says so, as its chart row's chip does."""
    st = data(int8_demo, "status")
    kinds = {}
    for f in st["findings"]:
        card = data(int8_demo, "show", f["criterion"])["card"]
        kind = [x for x in card["fields"] if x["key"] == "kind"]
        assert len(kind) == 1 and card["fields"][1]["key"] == "kind", card["fields"]
        kinds[f["criterion"]] = kind[0]["value"]
        text = run(int8_demo, "show", f["criterion"]).stdout
        assert re.search(rf"^KIND +{kind[0]['value']}$", text, re.M), text
    origin = {c["id"]: c.get("origin") for c in data(int8_demo, "status")["criteria"]} if "criteria" in st else {}
    assert "claim" in kinds.values() and "rule" in kinds.values(), kinds
    for cid, k in kinds.items():
        if cid in origin:
            assert (k == "claim") == (origin[cid] == "cited"), (cid, k, origin[cid])


@pytest.mark.parametrize("text", ["- rb: 1\n- kind: metric\n", '[{"rb": 1}, {"kind": "metric"}]\n'])
def test_a_list_that_starts_with_rb_is_read_as_a_record_file_not_a_file_card(int8_demo, text):
    """`rb show <file>.yaml` checks a record file: a list whose first item names rb is one, and not valid (exit 1)."""
    name = "list.json" if text.startswith("[") else "list.yaml"
    (Path(int8_demo.root) / name).write_text(text)
    r = run(int8_demo, "show", name, "--json")
    env = json.loads(r.stdout)
    assert r.returncode == 1 and "card" not in env["data"] and env["data"]["valid"] is False, r.stdout
    (Path(int8_demo.root) / "plain.yaml").write_text("a: 1\n")
    assert "card" in data(int8_demo, "show", "plain.yaml")
