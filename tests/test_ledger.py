"""The record's command surface (format 1), end to end, as a researcher and an agent drive it with --json.

Promises pinned here:

- `rb init "<title>"` creates `.rb/` (with its .gitignore and a union-merge .gitattributes for log.jsonl), refuses a
  second one, and every later command finds `.rb/` from any subdirectory; without one, commands say how to start.
- Ids are the kind's prefix plus six random base32 characters, or the id given; links (question, experiment) must
  name records that exist. A hypothesis is what an experiment tests; a note is words on an experiment or the project.
- Experiments hold variants with roles, what they vary on purpose, and settings; `<variant>.<setting>` is one variant's
  own value, and an undeclared difference between variants is a confound: an integrity problem, not a note.
- `rb spec set` records a setting from every source form (YAML/JSON/TOML key path, a YAML line turned into its key path,
  a quote, a run's JSON pointer, a prose line with --term) and verifies it when the source can be checked; a url or a
  note stays provisional without failing; a comment line never verifies; a source stating another value is a conflict
  that re-verifying never clears; a source outside the project is refused.
- `rb criterion add` takes exactly one rule; metric references (val_loss, int8.val_loss, candidate.val_loss, change.val_loss, aliases)
  read the right number from the runs; a finding is read on axes, never one word: its rule reads meets / misses /
  undetermined / untested for own criteria and reproduced / not reproduced for cited ones, a cited value that differs
  from ours leaves the rule undetermined as not comparable, and a provisional setting is a note, never a problem.
- `rb run add` records numbers with a receipt, reads --from files, per-run values, refuses a silent duplicate
  and flags a deliberate one.
- Anyone freezes, rules, amends or retracts, a human or an agent, and each call is recorded as whoever made it; a
  human's name typed inside an agent session is ignored and the agent recorded. A criterion an agent declares counts,
  attributed to the agent, and its timing says when it was declared.
- `rb status --fail-on` gates exit 1 only for the gates that trip; `rb compare`, `rb show`, `rb log`, `rb context` and
  `rb schema` return what they say; bad arguments are E_USAGE envelopes; every command a hint suggests exists.

Every test runs as a human (human:t) in no chat session, whatever environment runs pytest (a CI run's GITHUB_RUN_ID
included); agent cases set CLAUDECODE=1 themselves.
"""
import hashlib
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import as_the_user, cli_parser, command, environment, main, rbyaml

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
HUMAN = "human:t"

TRAIN_YAML = ("# training config\n"       # 1
              "model: resnet50\n"         # 2
              "optim:\n"                  # 3
              "  lr: 1e-4\n"              # 4
              "  betas: [0.9, 0.999]\n"   # 5
              "batch_size: 6\n"           # 6
              "amp: false\n"              # 7
              "log_every: 50\n")          # 8
TRAIN_PY = ("import argparse\n"
            "# lr = 0.1 is too high\n"
            "parser.add_argument('--lr', default=0.0001)\n"
            "warmup_steps = 500\n")
PAPER = ("We train with a learning rate of 0.0001 and batch 6.\n"
         "Ours achieves 1.43 validation loss on the held-out set (Table 3).\n")


# ---------------------------------------------------------------- fixtures and helpers


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_all(message="work"):
    if HAS_GIT:
        git("add", "-A")
        git("-c", "commit.gpgsign=false", "commit", "-qm", message)


@pytest.fixture
def lab(workdir, monkeypatch):
    """An empty project that rb records as human:t in no chat session, whatever environment runs the tests."""
    as_the_user(monkeypatch)
    monkeypatch.setenv("LOGNAME", "t")   # the fallback when git is missing still names the human t
    monkeypatch.setenv("USER", "t")
    if HAS_GIT:
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
    return workdir


@pytest.fixture
def project(lab):
    """A project with the files settings and cited numbers come from, committed when git is available."""
    Path("configs").mkdir()
    Path("configs/train.yaml").write_text(TRAIN_YAML, encoding="utf-8")
    Path("configs/eval.json").write_text(json.dumps({"data": {"crop": [224, 224]}, "iters": 12}), encoding="utf-8")
    Path("configs/train.toml").write_text("[train]\nepochs = 100\n", encoding="utf-8")
    Path("train.py").write_text(TRAIN_PY, encoding="utf-8")
    Path("paper.txt").write_text(PAPER, encoding="utf-8")
    Path("run1").mkdir()
    Path("run1/record.json").write_text(json.dumps({"seeds": {"torch": 1234}, "environment": {"cuda": "12.4"}}), encoding="utf-8")
    commit_all("sources")
    return lab


def rb(capsys, *argv):
    """Run rb with --json; return the exit code and the parsed envelope (exactly one JSON object on stdout)."""
    code = main([*argv, "--json"])
    return code, Envelope.model_validate_json(capsys.readouterr().out)


def ok(capsys, *argv) -> dict:
    code, env = rb(capsys, *argv)
    assert code == 0 and env.ok, (argv, [e.model_dump() for e in env.errors])
    return env.data


def refused(capsys, exit_code, error_code, *argv):
    code, env = rb(capsys, *argv)
    assert code == exit_code and not env.ok and env.errors[0].code == error_code, (argv, code, [e.model_dump() for e in env.errors])
    return env.errors[0].model_dump()


def start(capsys):
    return ok(capsys, "init", "INT8 quality")


def int8_experiment(capsys, *extra):
    """A project with experiment e1: fp32 (baseline) vs int8 (candidate), varying precision on purpose."""
    start(capsys)
    return ok(capsys, "experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8",
              "--varies", "precision", *extra)["object"]


def finding(capsys, criterion_id) -> dict:
    return ok(capsys, "show", criterion_id)["finding"]


def log_entries(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / ".rb" / "log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_sha(path: Path) -> str:
    """The hash the log keeps of a one-record file: its content, without the rb/kind header, however it is formatted."""
    data = rbyaml.loads(path.read_text(encoding="utf-8"))
    return rbyaml.content_sha({k: v for k, v in data.items() if k not in ("rb", "kind")})


# ---------------------------------------------------------------- init and finding .rb/


def test_init_creates_rb_with_its_git_files_and_logs_the_write(lab, capsys):
    data = start(capsys)
    proj = data["object"]
    assert proj["kind"] == "project" and proj["id"] == "int8-quality" and proj["title"] == "INT8 quality"
    assert proj["created_by"] == HUMAN and data["actor"]["id"] == HUMAN
    rbdir = lab / ".rb"
    assert set((rbdir / ".gitignore").read_text().split()) == {".lock", "*.tmp", "cache/", "journal/"}
    assert "log.jsonl merge=union" in (rbdir / ".gitattributes").read_text()
    assert (rbdir / "project.yaml").read_text(encoding="utf-8").startswith("rb: 1\nkind: project\n")
    assert {p.name for p in rbdir.iterdir()} - {".lock", "cache"} == {".gitattributes", ".gitignore", "log.jsonl", "project.yaml"}
    entry = log_entries(lab)[0]
    assert entry["op"] == "init" and entry["actor"] == HUMAN and entry["via"] == "cli" and entry["actor_via"]
    assert entry["path"] == ".rb/project.yaml" and entry["sha256"] == record_sha(rbdir / "project.yaml")


def test_a_second_init_is_refused_here_and_below(lab, capsys, monkeypatch):
    start(capsys)
    refused(capsys, 2, "E_PROJECT_EXISTS", "init", "again")
    Path("src/deep").mkdir(parents=True)
    monkeypatch.chdir("src/deep")
    refused(capsys, 2, "E_PROJECT_EXISTS", "init", "nested")
    assert not (lab / "src" / "deep" / ".rb").exists()


def test_commands_from_a_subdirectory_find_rb(lab, capsys, monkeypatch):
    start(capsys)
    Path("src/deep").mkdir(parents=True)
    monkeypatch.chdir("src/deep")
    q = ok(capsys, "question", "add", "Does INT8 hold up?")["object"]
    assert (lab / ".rb" / "questions" / f"{q['id']}.yaml").exists()


def test_without_rb_a_command_says_how_to_start_one(lab, capsys):
    err = refused(capsys, 2, "E_NO_PROJECT", "status")
    assert "rb init" in err["fix"] and "rb project" not in err["fix"], err["fix"]


# ---------------------------------------------------------------- ids and links


def test_ids_are_the_kind_prefix_and_six_random_characters(lab, capsys):
    int8_experiment(capsys)
    ids = {
        "q": ok(capsys, "question", "add", "q?")["object"]["id"],
        "e": ok(capsys, "experiment", "add", "x", "--hypothesis", "h")["object"]["id"],
        "c": ok(capsys, "criterion", "add", "c", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1")["object"]["id"],
        "run": ok(capsys, "run", "add", "e1", "int8.val_loss=0.5")["object"]["id"],
    }
    ids["d"] = ok(capsys, "decide", ids["e"], "investigate", "-m", "not sure yet")["object"]["id"]
    for prefix, id_ in ids.items():
        assert re.fullmatch(rf"{prefix}[a-z2-7]{{6}}", id_), (prefix, id_)
    assert len(set(ids.values())) == len(ids)


def test_a_chosen_id_is_kept_and_a_taken_one_is_refused(lab, capsys):
    start(capsys)
    assert ok(capsys, "question", "add", "q?", "--id", "q1")["object"]["id"] == "q1"
    err = refused(capsys, 2, "E_OBJECT_INVALID", "experiment", "add", "x", "--id", "q1")
    assert "already taken" in err["message"]


def test_an_experiments_hypothesis_links_to_its_question_and_a_missing_one_is_refused(lab, capsys):
    start(capsys)
    q = ok(capsys, "question", "add", "Can INT8 ship?")["object"]["id"]
    e = ok(capsys, "experiment", "add", "INT8 vs FP32", "--hypothesis", "INT8 keeps quality", "-q", q,
           "-m", "quantisation noise is small")["object"]
    assert e["questions"] == [q] and e["hypothesis"] == "INT8 keeps quality" and e["why"] == "quantisation noise is small"
    assert ok(capsys, "show", e["id"])["state"] == "planned"
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", "experiment", "add", "x", "--hypothesis", "h", "-q", "qnone")


def test_an_experiment_is_planned_until_a_run_tests_it(lab, capsys):
    """A hypothesis is what an experiment tests: the experiment stands as a planned direction until its first run, then
    it is open (evidence, no call yet). rb records what ran, never that something is running."""
    start(capsys)
    e = ok(capsys, "experiment", "add", "INT8 vs FP32", "--id", "e1", "--hypothesis", "INT8 keeps quality",
           "--baseline", "fp32", "--candidate", "int8")["object"]
    assert e["hypothesis"] == "INT8 keeps quality"
    assert ok(capsys, "show", "e1")["state"] == "planned"
    assert ("planned", "e1") in [(o["code"], o["subject"]) for o in ok(capsys, "status")["open"]]
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")
    assert ok(capsys, "show", "e1")["state"] == "open"
    assert "planned" not in [o["code"] for o in ok(capsys, "status")["open"]]


def test_a_note_applies_to_an_existing_experiment_or_the_project(lab, capsys):
    int8_experiment(capsys)
    n = ok(capsys, "note", "add", "the held-out set is not in the pretraining set", "-e", "e1")["object"]
    assert (n["kind"], n["experiment_id"], n["text"], n["by"]) == ("note", "e1", "the held-out set is not in the pretraining set", HUMAN)
    assert [x["text"] for x in ok(capsys, "show", "e1")["object"]["notes"]] == ["the held-out set is not in the pretraining set"]
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", "note", "add", "x", "-e", "enone")
    ok(capsys, "note", "add", "the evaluation split is fixed")
    assert [x["text"] for x in ok(capsys, "status")["project"]["notes"]] == ["the evaluation split is fixed"]


# ---------------------------------------------------------------- experiments, variants, metrics


def test_experiment_add_records_variants_with_roles_and_what_varies(lab, capsys):
    start(capsys)
    e = ok(capsys, "experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8",
           "--candidate", "int4", "--varies", "precision,bits")["object"]
    assert e["kind"] == "experiment" and e["id"] == "e1"
    assert [(v["name"], v["role"]) for v in e["variants"]] == [("fp32", "baseline"), ("int8", "candidate"), ("int4", "candidate")]
    assert e["varies"] == ["precision", "bits"]


def test_experiment_like_copies_shared_settings_as_provisional_and_what_it_varies(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    e2 = ok(capsys, "experiment", "add", "rerun", "--id", "e2", "--like", "e1")["object"]
    [lr] = e2["settings"]
    assert lr["name"] == "lr" and lr["value"] == 0.0001 and lr["status"] == "provisional"
    assert lr["source"]["key"] == "optim.lr" and lr["source"]["resolved"] is None
    assert e2["varies"] == ["precision"]


def test_variant_add_adds_an_arm_with_its_role(lab, capsys):
    int8_experiment(capsys)
    v = ok(capsys, "variant", "add", "e1", "no-quant-head", "--role", "ablation")["object"]
    assert v["kind"] == "variant" and v["experiment_id"] == "e1" and v["name"] == "no-quant-head" and v["role"] == "ablation"
    variants = ok(capsys, "show", "e1")["object"]["variants"]
    assert ("no-quant-head", "ablation") in [(x["name"], x["role"]) for x in variants]


def test_an_experiment_compares_against_one_baseline(lab, capsys):
    int8_experiment(capsys)
    err = refused(capsys, 2, "E_OBJECT_INVALID", "variant", "add", "e1", "fp16", "--role", "baseline")
    assert "already has a baseline" in err["message"]


def test_metric_add_records_unit_direction_and_aliases(lab, capsys):
    start(capsys)
    m = ok(capsys, "metric", "add", "val_loss", "--unit", "nats", "--minimize", "--alias", "cross_entropy")["object"]
    assert (m["kind"], m["id"], m["unit"], m["direction"], m["aliases"]) == ("metric", "val_loss", "nats", "minimize", ["cross_entropy"])
    assert ok(capsys, "metric", "add", "acc", "--maximize")["object"]["direction"] == "maximize"
    assert ok(capsys, "metric", "add", "gpu_hours")["object"]["direction"] == "none"
    refused(capsys, 2, "E_OBJECT_INVALID", "metric", "add", "val_loss2", "--alias", "cross_entropy")


def test_a_criterion_reads_a_run_reported_under_a_metric_alias(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--unit", "nats", "--minimize", "--alias", "cross_entropy")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    ok(capsys, "run", "add", "e1", "int8.cross_entropy=1.2")
    [obs] = finding(capsys, "c1")["observations"]
    assert obs["value"] == 1.2 and obs["counted"] and not obs["before_declared"]


# ---------------------------------------------------------------- spec set: every source form


@pytest.mark.parametrize("name, source, value, line", [
    ("lr", "configs/train.yaml#optim.lr", 0.0001, 4),          # YAML's 1e-4 is read as a number, not text
    ("crop", "configs/eval.json#data.crop", [224, 224], None),
    ("epochs", "configs/train.toml#train.epochs", 100, None),
], ids=["yaml", "json", "toml"])
def test_spec_set_reads_a_key_path_and_verifies_it(project, capsys, name, source, value, line):
    int8_experiment(capsys)
    data = ok(capsys, "spec", "set", "e1", name, "--source", source)     # no value given: rb reads it at the key
    s = data["object"]
    assert s["kind"] == "setting" and s["address"] == f"e1/{name}"
    assert s["value"] == value and s["status"] == "verified" and data["outcome"] == {"passed": True, "failures": []}
    assert s["source"]["resolved"]["line"] == line
    if HAS_GIT:
        assert s["source"]["resolved"]["commit"] == git("rev-parse", "HEAD")   # a committed, unmodified file is pinned


def test_spec_set_turns_a_yaml_line_into_its_key_path(project, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "batch_size", "6", "--source", "configs/train.yaml:6")["object"]
    assert s["source"]["key"] == "batch_size" and s["source"]["line"] is None and s["status"] == "verified"


def test_spec_set_verifies_a_quote_on_its_line(project, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "amp", "false", "--source", "configs/train.yaml", "--quote", "amp: false")["object"]
    assert s["value"] is False and s["status"] == "verified" and s["source"]["resolved"]["line"] == 7


@pytest.mark.parametrize("name, value, source, expected", [
    ("seeds.torch", None, "run:run1#/seeds/torch", 1234),                   # a run directory means its record.json
    ("cuda", '"12.4"', "run:run1/record.json#/environment/cuda", "12.4"),   # text that looks like a number stays text
], ids=["number", "text"])
def test_spec_set_verifies_a_value_at_a_run_pointer(project, capsys, name, value, source, expected):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", name, *([value] if value else []), "--source", source)
    s = env.data["object"]
    assert s["value"] == expected and s["source"]["kind"] == "run"
    assert code == 0 and s["status"] == "verified" and s["conflict"] is None, env.data["outcome"]


def test_a_prose_line_verifies_only_with_the_settings_word(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "learning_rate", "0.0001", "--source", "paper.txt:1")
    assert code == 1 and env.data["object"]["status"] == "provisional"
    s = ok(capsys, "spec", "set", "e1", "learning_rate", "0.0001", "--source", "paper.txt:1", "--term", "learning rate")["object"]
    assert s["status"] == "verified" and s["source"]["term"] == "learning rate"


def test_a_comment_line_never_verifies(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "lr", "0.1", "--source", "train.py:2", "--term", "lr")
    [failure] = env.data["outcome"]["failures"]
    assert code == 1 and env.data["object"]["status"] == "provisional"
    assert failure["code"] == "E_SOURCE_UNRESOLVED" and "comment" in failure["reason"]
    assert 3 in [c["line"] for c in failure["candidates"]]     # it points at the line that does set lr


DOCS = {
    "README.md": "# Setup\n**Learning rate:** 3e-4\n## lr = 3e-4\n```yaml\n# lr: 0.1\n```\n<!-- lr: 0.1 -->\n## Config\n    # lr: 0.1\n",
    "NOTES.md": "Start an HTML comment with `<!--`.\n\nTrain at lr 3e-4.\n",
    "LIST.md": "- Optimizer\n\n    * **lr:** 3e-4\n\n1. Stage 2\n\n    **lr:** 3e-4\n",
    "RUN.md": "```bash\npython train.py --lr 3e-4\n# python train.py --lr 0.1\n```\n",
    "doc.rst": "- Optimizer:\n\n  * lr: 3e-4\n\n.. note::\n\n   **lr:** 3e-4\n\n.. code-block:: yaml\n\n   # lr: 0.1\n",
    "cfg.yaml": "# lr: 0.1\nlr: 0.0003\n",
    "paper.tex": "% lr = 0.1 diverged\nWe set lr = 3e-4.\n",
    "requirements.txt": "# torch==2.3.0\ntorch==2.4.1\n",
    "CMakeLists.txt": "project(x)\n# set(BATCH 64)\nset(BATCH 32)\n",
    "app.conf": "// lr = 0.1\nlr = 0.0003\n",
    "STEPS.md": "1. Train the model:\n\n        python train.py --lr 3e-4\n        # python train.py --lr 1e-3   (diverged)\n",
    "LAZY.md": "1. Install:\n   ```bash\n   pip install -e .\n   ```\nThen train:\n\n    # python train.py --lr 1e-3\n",
    "FENCES.md": "```ini\n; lr = 0.1\n```\n```c\n/* float lr = 0.2; */\n```\n",
    "FRONT.md": "---\ntitle: x\n# lr: 0.1\nlr: 3e-4\n---\n",
    "api.rst": ".. py:function:: train(lr=3e-4, warmup=500)\n\n   Trains the model at lr 3e-4.\n",
    "report.qmd": "# R\n\n**Learning rate:** 3e-4\n",
    "notes.txt": "Final configuration (run #3): lr 3e-4, batch size 256\n# lr: 1e-3 diverged\n",
    "NOTES": "Final configuration (run #3): lr 3e-4, batch size 256\n",
    "report.html": "<tr><td>Run #3</td><td>lr 3e-4</td></tr>\n",
    "LOG.md": "```\n[run #3] lr 3e-4 | warmup 500\n```\n```text\n[run #4] lr 3e-4 | warmup 400\n```\n",
    "verb.tex": "\\begin{verbatim}\noptim:\n  # lr: 1e-3\n  lr: 3e-4\n\\end{verbatim}\n\\begin{minted}{python}\n# lr = 1e-3\n\\end{minted}\n",
    "ipy.rst": ".. ipython:: python\n\n   # lr = 1e-3\n   lr = 3e-4\n\n.. testcode::\n\n   # trainer = Trainer(lr=1e-3)\n",
}


@pytest.mark.parametrize("name, source, term, value, comment", [
    ("lr", "README.md:2", "learning rate", "0.0003", False),     # **bold** is text in Markdown
    ("lr", "README.md:3", None, "0.0003", False),                # so is a heading
    ("lr", "README.md:5", None, "0.1", True),                    # a # line in a code block is a comment
    ("lr", "README.md:7", None, "0.1", True),                    # so is an HTML comment
    ("lr", "README.md:9", None, "0.1", True),                    # and a # line indented as code under a heading
    ("lr", "NOTES.md:3", None, "0.0003", False),                 # a <!-- in a code span opens no comment
    ("lr", "LIST.md:3", None, "0.0003", False),                  # an item indented in a list is text
    ("lr", "LIST.md:7", None, "0.0003", False),                  # so is a paragraph indented in a list
    ("lr", "RUN.md:2", None, "0.0003", False),                   # a command in a code block is no comment
    ("lr", "RUN.md:3", None, "0.1", True),
    ("lr", "doc.rst:3", None, "0.0003", False),                  # a nested reStructuredText bullet is text
    ("lr", "doc.rst:7", None, "0.0003", False),                  # so is a note's text
    ("lr", "doc.rst:11", None, "0.1", True),                     # a # line in a code-block is a comment
    ("lr", "cfg.yaml:1", None, "0.1", True),
    ("lr", "paper.tex:1", None, "0.1", True),
    ("lr", "paper.tex:2", None, "0.0003", False),
    ("env.torch", "requirements.txt:1", None, "2.3.0", True),    # requirements.txt comments with #
    ("env.torch", "requirements.txt:2", None, "2.4.1", False),
    ("batch", "CMakeLists.txt:2", "BATCH", "64", True),          # so does CMakeLists.txt
    ("lr", "app.conf:1", None, "0.1", True),                     # a .conf file may be HOCON's, with //
    ("lr", "STEPS.md:4", None, "0.001", True),                   # a code block in a list item is code
    ("lr", "LAZY.md:7", None, "0.001", True),                    # a fence closed in an item carries no paragraph on
    ("lr", "FENCES.md:2", None, "0.1", True),                    # a fence reads by its language: ini's ;
    ("lr", "FENCES.md:5", None, "0.2", True),                    # and c's /*
    ("lr", "FRONT.md:3", None, "0.1", True),                     # front matter is YAML
    ("lr", "api.rst:3", None, "0.0003", False),                  # a Sphinx directive's text is text
    ("lr", "report.qmd:3", "learning rate", "0.0003", False),    # Quarto is Markdown
    ("lr", "notes.txt:1", None, "0.0003", False),                # a # in prose ends nothing: run #3 is text
    ("lr", "notes.txt:2", None, "0.001", True),                  # and a line opening with one is a comment, as before
    ("lr", "NOTES:1", None, "0.0003", False),                    # so in a file with no extension
    ("lr", "report.html:1", None, "0.0003", False),              # and in a web page
    ("warmup", "LOG.md:2", None, "500", False),                  # and in a code block that names no language
    ("warmup", "LOG.md:5", None, "400", False),                  # or one rb does not know
    ("lr", "verb.tex:3", None, "0.001", True),                   # a verbatim environment's code is code
    ("lr", "verb.tex:4", None, "0.0003", False),
    ("lr", "verb.tex:7", None, "0.001", True),                   # and minted's, in the language it names
    ("lr", "ipy.rst:3", None, "0.001", True),                    # so is an ipython directive's
    ("lr", "ipy.rst:4", None, "0.0003", False),
    ("trainer", "ipy.rst:8", "lr", "0.001", True),               # and a testcode directive's
], ids=["md-bold", "md-heading", "md-code-block", "md-html-comment", "md-indented-code", "md-inline-html", "md-list-item",
        "md-list-paragraph", "md-command", "md-commented-command", "rst-bullet", "rst-note", "rst-code-block", "yaml-hash",
        "tex-percent", "tex-text", "txt-hash", "txt-text", "cmake-hash", "conf-slashes", "md-list-code", "md-after-fence",
        "md-ini-fence", "md-c-fence", "md-front-matter", "rst-sphinx", "qmd-bold", "prose-txt", "prose-txt-hash-line",
        "prose-no-extension", "prose-html", "md-unnamed-fence", "md-text-fence", "tex-verbatim", "tex-verbatim-text",
        "tex-minted", "rst-ipython", "rst-ipython-text", "rst-testcode"])
def test_a_comment_is_read_by_its_file_s_own_syntax(project, capsys, name, source, term, value, comment):
    int8_experiment(capsys)
    for path, text in DOCS.items():
        Path(path).write_text(text, encoding="utf-8")
    code, env = rb(capsys, "spec", "set", "e1", name, value, "--source", source, *(["--term", term] if term else []))
    if not comment:
        assert code == 0 and env.data["object"]["status"] == "verified", env.data["outcome"]
        return
    [failure] = env.data["outcome"]["failures"]
    assert code == 1 and env.data["object"]["status"] == "provisional" and "comment" in failure["reason"]


def test_a_comment_states_no_analysis_s_numbers(project, capsys):
    int8_experiment(capsys)
    Path("README.md").write_text("<!-- int8 vs fp32: val_loss 0.023 [0.014, 0.032] -->\n", encoding="utf-8")
    Path("REPORT.md").write_text("# Report\n\nint8 vs fp32: val_loss 0.023 [0.014, 0.032]\n", encoding="utf-8")
    compare = ("analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.023[0.014,0.032]", "--source")
    error = refused(capsys, 2, "E_SOURCE_UNRESOLVED", *compare, "README.md")
    assert "README.md:1 is a comment" in error["message"]
    a = ok(capsys, *compare, "REPORT.md")["object"]
    assert a["comparisons"][0]["source"]["line"] == 3
    Path("REPORT.md").write_text("# Report\n\n<!-- int8 vs fp32: val_loss 0.023 [0.014, 0.032] -->\n", encoding="utf-8")
    assert "REPORT.md:3 is a comment, and a comment states nothing" in json.dumps(ok(capsys, "show", a["id"]))


def test_output_states_an_analysis_s_numbers_whatever_mark_opens_its_line(project, capsys):
    """A log, a slurm output, a results .txt and output pasted into a block that names no language have no comments of
    their own: ' * Acc@1 81.800' is what the eval printed and run #3 is text, so each line states the numbers on it, and
    goes on stating them when the next eval is appended to the log."""
    int8_experiment(capsys)
    Path("logs").mkdir()
    printed = "Test: [100/100] Time 0.120\n * Acc@1 81.800 Acc@5 95.594 loss 0.796\n"
    Path("logs/eval.log").write_text(printed, encoding="utf-8")
    Path("logs/slurm-4242.out").write_text(printed, encoding="utf-8")
    Path("logs/sweep.log").write_text("Seed sweep, run #3: int8 vs fp32 top1 +0.021 [+0.012, +0.030]\n", encoding="utf-8")
    Path("results.txt").write_text("Results\n\n* int8 vs fp32: val_loss +0.023 [+0.014, +0.032] (paired bootstrap over images)\n", encoding="utf-8")
    Path("RESULTS.md").write_text("# Results\n\n```\n * Acc@1 81.800 Acc@5 95.594\n```\n", encoding="utf-8")
    ok(capsys, "criterion", "add", "INT8 keeps top-1", "-e", "e1", "--metric", "int8.acc1", "--at-least", "80", "--id", "c1")
    for compare, source, line in (("int8:acc1=81.8", "logs/eval.log", 2), ("int8:loss=0.796", "logs/slurm-4242.out", 2),
                                  ("int8/fp32:top1=0.021[0.012,0.030]", "logs/sweep.log", 1),
                                  ("int8/fp32:val_loss=0.023[0.014,0.032]", "results.txt", 3), ("int8:acc5=95.594", "RESULTS.md", 4)):
        a = ok(capsys, "analysis", "add", "e1", "--compare", compare, "--source", source, "--method", "eval script")["object"]
        assert a["comparisons"][0]["source"]["line"] == line, source
    with Path("logs/eval.log").open("a", encoding="utf-8") as f:
        f.write("Test: [100/100] Time 0.118\n * Acc@1 81.802 Acc@5 95.600 loss 0.795\n")
    assert finding(capsys, "c1")["integrity"] == []
    assert rb(capsys, "status", "--fail-on", "stale")[0] == 0


def test_prose_states_a_cited_number_and_an_analysis_s_beside_a_hash(project, capsys):
    """A paper or a report saved as text says #heads 12 and run #3 as text, so a setting, a cited number and an
    analysis's numbers on such a line are stated by it, and every read after says so."""
    int8_experiment(capsys)
    Path("papers").mkdir()
    Path("papers/vit.txt").write_text("Model setup. ViT-B/16 uses 12 layers, #heads 12, hidden size 768 and dropout 0.1.\n"
                                      "With 4x fewer #params, DeiT-S reaches 79.8% top-1 on ImageNet.\n", encoding="utf-8")
    Path("results.txt").write_text("Evaluation report\n\nRun #3 (seed sweep): int8 vs fp32 top1 +0.021 [+0.012, +0.030]\n", encoding="utf-8")
    commit_all("the paper and the report")
    assert ok(capsys, "spec", "set", "e1", "dropout", "0.1", "--source", "papers/vit.txt:1")["object"]["status"] == "verified"
    ok(capsys, "criterion", "add", "DeiT-S top-1", "-e", "e1", "--metric", "fp32.top1", "--equals", "79.8", "--tolerance", "0.1",
       "--source", "papers/vit.txt", "--quote", "DeiT-S reaches 79.8% top-1", "--locator", "Sec. 4", "--id", "c1")
    a = ok(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:top1=0.021[0.012,0.030]", "--source", "results.txt", "--method", "bootstrap")["object"]
    assert a["comparisons"][0]["source"]["line"] == 3
    assert finding(capsys, "c1")["integrity"] == [] and rb(capsys, "status", "--fail-on", "stale")[0] == 0


def test_a_comment_that_ends_a_line_or_holds_a_quote_states_nothing(project, capsys):
    """A comment that ends a line is not read, by the syntax the line is read in (a requirements file's #, a code
    block's language, Markdown's <!-- -->), so a value only it names does not verify and a pin changed beside one is no
    longer stated; a quote is found on the first line that is no comment, for a setting and for an analysis."""
    int8_experiment(capsys)
    Path("HP.md").write_text("# HP\n```python\nlearning_rate = 3e-4  # lr = 1e-3 in the paper\n```\n", encoding="utf-8")
    Path("OPT.md").write_text("**Optimizer:** AdamW <!-- SGD diverged -->\n", encoding="utf-8")
    Path("requirements.txt").write_text("torch==2.4.1\n", encoding="utf-8")
    Path("NOTES.md").write_text("<!-- Batch size: 256 -->\n\nBatch size: 256\n", encoding="utf-8")
    Path("REPORT.md").write_text("<!-- top1 0.021 [0.012, 0.030] -->\nSee the table <!-- top1 0.021 [0.012, 0.030] -->\n"
                                 "| cand vs base | top1 0.021 [0.012, 0.030] |\n", encoding="utf-8")
    for name, value, source in (("lr", "0.001", "HP.md:3"), ("optimizer", "SGD", "OPT.md:1")):
        assert rb(capsys, "spec", "set", "e1", name, value, "--source", source, "--term", name)[0] == 1, source
    for name, value, extra in (("optimizer", "AdamW", ("--source", "OPT.md:1")), ("env.torch", "2.4.1", ("--source", "requirements.txt:1")),
                               ("batch", "256", ("--source", "NOTES.md", "--quote", "Batch size: 256", "--term", "batch size"))):
        assert ok(capsys, "spec", "set", "e1", name, value, *extra)["object"]["status"] == "verified", name
    Path("requirements.txt").write_text("torch==2.5.0  # was 2.4.1\n", encoding="utf-8")
    assert rb(capsys, "spec", "verify", "e1", "env.torch")[0] == 1
    a = ok(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:top1=0.021[0.012,0.030]", "--source", "REPORT.md",
           "--quote", "top1 0.021 [0.012, 0.030]")["object"]
    assert a["comparisons"][0]["source"]["line"] == 3


@pytest.mark.parametrize("source, kind", [("https://example.com/env", "url"), ("note:the authors said so in an issue", "note")], ids=["url", "note"])
def test_url_and_note_sources_stay_provisional_without_failing(project, capsys, source, kind):
    int8_experiment(capsys)
    data = ok(capsys, "spec", "set", "e1", "gpu", "A5000", "--source", source)
    assert data["object"]["status"] == "provisional" and data["object"]["source"]["kind"] == kind
    assert data["outcome"]["passed"]
    data = ok(capsys, "spec", "verify", "e1", "gpu")
    [row] = data["results"]
    assert row["skipped"] and row["code"] == "E_SOURCE_UNVERIFIABLE" and row["after"] == "provisional" and data["outcome"]["passed"]


def test_spec_set_unknown_records_a_setting_nobody_has_found(lab, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "seed", "--unknown")["object"]
    assert s["status"] == "unknown" and s["value"] is None and s["required"]
    st = ok(capsys, "status")
    assert st["gate"]["unknown"] and ("unknown", "e1/seed") in [(o["code"], o["subject"]) for o in st["open"]]


def test_a_per_run_setting_takes_its_value_from_each_run(lab, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "seed", "--per-run")["object"]
    assert s["per_run"] and s["value"] is None
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", "--set", "seed=2")["object"]
    assert run["per_run"] == {"seed": 2}
    err = refused(capsys, 2, "E_OBJECT_INVALID", "run", "add", "e1", "int8.val_loss=1.4", "--set", "lr=2")
    assert "rb spec set e1 lr --per-run" in err["message"]


def test_spec_set_from_a_config_records_only_the_chosen_keys(project, capsys):
    int8_experiment(capsys)
    data = ok(capsys, "spec", "set", "e1", "--from", "configs/train.yaml", "--keys", "optim.*,model")
    got = {s["address"]: (s["value"], s["status"]) for s in data["object"]["settings"]}
    assert got == {"e1/optim.lr": (0.0001, "verified"), "e1/optim.betas": ([0.9, 0.999], "verified"), "e1/model": ("resnet50", "verified")}
    err = refused(capsys, 2, "E_OBJECT_INVALID", "spec", "set", "e1", "--from", "configs/train.yaml")
    assert "--keys" in err["message"]
    for keys, said in (("batch_size", "1 setting"), ("optim.*,model", "3 settings")):      # one and many, said so
        assert main(["spec", "set", "e1", "--from", "configs/train.yaml", "--keys", keys]) == 0
        n = said.split()[0]
        assert re.match(rf"e1: {said} from configs/train\.yaml( @\w+)? · {n} verified\n", capsys.readouterr().out)


def test_a_source_of_one_key_or_one_line_is_counted_as_one(project, capsys):
    """A config of one key given no --keys, and a file asked for a line past its end, say how many keys or lines it
    has in the singular when it is one: with a value given and without one."""
    int8_experiment(capsys)
    Path("one.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    Path("one.py").write_text("lr = 0.1\n", encoding="utf-8")
    Path("two.py").write_text("lr = 0.1\nwd = 0.01\n", encoding="utf-8")
    err = refused(capsys, 2, "E_OBJECT_INVALID", "spec", "set", "e1", "--from", "one.yaml")
    assert err["message"] == 'one.yaml has 1 key; pick it with --keys if it defines the experiment: --keys "lr"'
    for name, source, said in (("lr", "one.py:5", "one.py has 1 line, not 5"), ("wd", "one.yaml:4", "one.yaml has 1 line, not 4"),
                               ("bs", "two.py:5", "two.py has 2 lines, not 5")):
        code, env = rb(capsys, "spec", "set", "e1", name, "0.1", "--source", source)
        assert code == 1 and [f["reason"] for f in env.data["outcome"]["failures"]] == [said]
    err = refused(capsys, 2, "E_SOURCE_UNRESOLVED", "spec", "set", "e1", "eps", "--source", "one.py:5")    # the value read there
    assert err["message"] == "one.py has 1 line, not 5."


@needs_git
def test_a_command_asks_git_each_source_question_once(project, capsys, tmp_path, monkeypatch):
    """Reading the settings again asks git which commit each was verified at, and whether HEAD holds a file git does
    not track; the answer, a missing one too, holds for the whole command however many settings read that file."""
    int8_experiment(capsys)
    Path("notes.yaml").write_text("a: 1\nb: 2\nc: 3\n", encoding="utf-8")       # untracked: HEAD has no notes.yaml
    for key in "abc":
        ok(capsys, "spec", "set", "e1", key, "--source", f"notes.yaml#{key}")
    sha = git("rev-parse", "HEAD")
    for name, key in [("lr", "optim.lr"), ("betas", "optim.betas"), ("batch_size", "batch_size")]:
        ok(capsys, "spec", "set", "e1", name, "--source", f"configs/train.yaml#{key}", "--commit", sha)
    trace = tmp_path / "git-trace.txt"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    ok(capsys, "status")
    asked = [line.split("trace: built-in: git ", 1)[1] for line in trace.read_text(encoding="utf-8").splitlines() if "trace: built-in: git " in line]
    assert asked.count("show HEAD:notes.yaml") == 1, asked
    commits = [a for a in asked if a.startswith("rev-parse") and "^{commit}" in a]
    assert len(commits) == 1, commits                                          # three settings read at one commit


@needs_git
def test_a_command_asks_git_about_each_tree_a_run_names_once(project, capsys, tmp_path, monkeypatch):
    """Runs made before they were recorded that name the tree they ran from have their code there: whether each tree is
    here, and which tree their commit has, are asked once a command, however often it reads what a rerun needs."""
    int8_experiment(capsys)
    head = git("rev-parse", "HEAD")
    trees = []
    for n in range(3):
        Path("train.py").write_text(f"print({n})\n", encoding="utf-8")
        git("add", "train.py")
        trees.append(git("write-tree"))
        git("reset", "-q", "train.py")
        ok(capsys, "run", "add", "e1", f"int8.val_loss=1.{n}", "--produced-at", "2026-05-10", "--commit", head, "--tree", trees[-1])
    trace = tmp_path / "git-trace.txt"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    ok(capsys, "status")
    asked = [line.split("trace: built-in: git ", 1)[1] for line in trace.read_text(encoding="utf-8").splitlines() if "trace: built-in: git " in line]
    assert [sum(t in a for a in asked) for t in trees] == [1, 1, 1], asked
    assert sum(f"{head}^{{tree}}" in a for a in asked) == 1, [a for a in asked if "tree" in a]


def test_a_variant_setting_overrides_the_shared_one_for_that_variant(lab, capsys):
    int8_experiment(capsys)
    s = ok(capsys, "spec", "set", "e1", "int8.precision", "int8", "--source", "note:quantised export")["object"]
    assert s["address"] == "e1/int8.precision" and s["name"] == "precision"
    assert ok(capsys, "spec", "set", "e1", "precision", "fp16", "--variant", "fp32")["object"]["address"] == "e1/fp32.precision"
    exp = ok(capsys, "show", "e1")["object"]
    own = {v["name"]: {x["name"]: x["value"] for x in v["settings"]} for v in exp["variants"]}
    assert own == {"fp32": {"precision": "fp16"}, "int8": {"precision": "int8"}} and exp["settings"] == []
    assert ok(capsys, "show", "e1/int8.precision")["object"]["value"] == "int8"


def test_a_difference_between_variants_not_declared_in_varies_is_a_confound_problem(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "int8.precision", "int8", "--source", "note:x")
    ok(capsys, "spec", "set", "e1", "fp32.precision", "fp32", "--source", "note:x")
    assert not [c for c in ok(capsys, "show", "e1")["caveats"] if c["code"] == "confound"]    # declared: the question itself
    ok(capsys, "spec", "set", "e1", "int8.calib_batches", "64", "--source", "note:x")
    [confound] = [c for c in ok(capsys, "show", "e1")["caveats"] if c["code"] == "confound"]
    assert confound["problem"] and "calib_batches" in confound["subject"]


# ---------------------------------------------------------------- spec verify


def test_spec_set_no_verify_hints_spec_verify_which_verifies_it(project, capsys):
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "warmup_steps", "500", "--source", "train.py:4", "--no-verify")
    assert code == 0 and env.data["object"]["status"] == "provisional" and env.next == ["rb spec verify e1 warmup_steps"]
    data = ok(capsys, "spec", "verify", "e1", "warmup_steps")
    [row] = data["results"]
    assert row["after"] == "verified" and row["read"] == "warmup_steps = 500" and row["line"] == 4 and data["verified_count"] == 1


def test_spec_verify_exits_1_and_keeps_a_conflict_until_the_value_changes(project, capsys):
    """configs/train.yaml never stated 16, so the conflict says what it states, never what it now states: it did not
    change."""
    int8_experiment(capsys)
    code, env = rb(capsys, "spec", "set", "e1", "batch_size", "16", "--source", "configs/train.yaml#batch_size")
    said = "the record says 16, but configs/train.yaml#batch_size says 6"                          # the record's value first
    assert code == 1 and env.data["object"]["conflict"]["text"] == said
    code, env = rb(capsys, "spec", "verify", "e1", "batch_size")
    [row] = env.data["results"]
    assert code == 1 and env.ok and not env.data["outcome"]["passed"]
    assert row["conflict"] and row["after"] == "provisional"
    assert row["reason"] == said
    s = ok(capsys, "spec", "set", "e1", "batch_size", "6", "--source", "configs/train.yaml#batch_size")["object"]
    assert s["status"] == "verified" and s["conflict"] is None


def test_now_in_a_source_s_own_words_never_says_the_source_changed(project, capsys):
    """Whether a conflict says what its source now says is read in rb's own words: "now says" in the line it quotes, or
    in a place's --locator, leaves every rb spec verify saying what the source says, as the first rb spec set did."""
    int8_experiment(capsys)
    Path("notes.md").write_text("Changelog: the lr now says 0.3 in the final config.\n", encoding="utf-8")
    Path("cfg.yaml").write_text("wd: 0.3\n", encoding="utf-8")
    commit_all("sources")
    said = {"lr": "the record says 0.1, but notes.md:1 says 0.3; it reads: Changelog: the lr now says 0.3 in the final config.",
            "wd": "the record says 0.1, but cfg.yaml#wd (the table that now says 0.3) says 0.3"}
    assert rb(capsys, "spec", "set", "e1", "lr", "0.1", "--source", "notes.md:1")[0] == 1
    assert rb(capsys, "spec", "set", "e1", "wd", "0.1", "--source", "cfg.yaml#wd", "--locator", "the table that now says 0.3")[0] == 1
    for _ in range(2):
        code, env = rb(capsys, "spec", "verify", "e1", "lr", "wd")
        assert code == 1 and {row["setting"]: row["reason"] for row in env.data["results"]} == said
        assert {n: ok(capsys, "show", f"e1/{n}")["object"]["conflict"]["text"] for n in said} == said


def test_a_source_outside_the_project_is_refused(lab, capsys, tmp_path_factory):
    int8_experiment(capsys)
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "cfg.yaml"
    elsewhere.write_text("lr: 0.1\n", encoding="utf-8")
    code, env = rb(capsys, "spec", "set", "e1", "lr", "0.1", "--source", f"{elsewhere}#lr")
    [failure] = env.data["outcome"]["failures"]
    assert code == 1 and failure["code"] == "E_SOURCE_OUTSIDE" and env.data["object"]["status"] == "provisional"


# ---------------------------------------------------------------- criteria


@pytest.mark.parametrize("flags, comparator, rule", [
    (["--at-most", "1.5"], "at_most", "int8.val_loss ≤ 1.5"),
    (["--at-least", "1"], "at_least", "int8.val_loss ≥ 1"),
    (["--equals", "1.43", "--tolerance", "0.05"], "equals", "int8.val_loss = 1.43 ± 0.05"),
    (["--at-most", "-1e-3"], "at_most", "int8.val_loss ≤ -0.001"),     # a negative number is a value, not an option
], ids=["at-most", "at-least", "equals", "negative-target"])
def test_criterion_add_records_each_comparator(lab, capsys, flags, comparator, rule):
    int8_experiment(capsys)
    data = ok(capsys, "criterion", "add", "INT8 validation loss", "-e", "e1", "--metric", "int8.val_loss", *flags)
    assert data["object"]["kind"] == "criterion" and data["object"]["comparator"] == comparator and data["object"]["origin"] == "own"
    f = data["finding"]
    assert f["rule"] == rule and (f["rule_outcome"], f["result"], f["timing"]) == ("untested", "untested", "untested")


@pytest.mark.parametrize("flags", [["--at-most", "1", "--at-least", "0"], ["--equals", "1"], ["--at-most", "1", "--tolerance", "0.1"], []],
                         ids=["two-rules", "equals-without-tolerance", "tolerance-without-equals", "none"])
def test_a_criterion_needs_exactly_one_rule(lab, capsys, flags):
    int8_experiment(capsys)
    refused(capsys, 2, "E_OBJECT_INVALID", "criterion", "add", "x", "-e", "e1", "--metric", "int8.val_loss", *flags)
    criteria = lab / ".rb" / "experiments" / "e1" / "criteria"
    assert not criteria.exists() or not list(criteria.iterdir())


@pytest.mark.parametrize("metric, expected", [("val_loss", 1.42), ("int8.val_loss", 1.42), ("candidate.val_loss", 1.42),
                                              ("baseline.val_loss", 1.38), ("change.val_loss", 1.42 - 1.38)])
def test_a_metric_reference_reads_the_right_number(lab, capsys, metric, expected):
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "reads", "-e", "e1", "--metric", metric, "--at-most", "5", "--id", "c1")
    ok(capsys, "run", "add", "e1", "fp32.val_loss=1.38", "int8.val_loss=1.42")
    [obs] = finding(capsys, "c1")["observations"]
    assert obs["value"] == pytest.approx(expected)


# ---------------------------------------------------------------- rule words


@pytest.mark.parametrize("cited, values, outcome, word", [
    (False, [], "untested", "criterion untested"),
    (False, ["1.2"], "meets", "estimate meets criterion"),
    (False, ["1.8"], "misses", "criterion not met"),
    (False, ["1.2", "1.6"], "meets", "estimate meets criterion"),      # one run over 1.5: the rule reads the mean over units (1.4), not a vote
    (True, [], "untested", "criterion untested"),
    (True, ["1.44"], "meets", "reproduced"),
    (True, ["1.60"], "misses", "not reproduced"),
])
def test_the_rule_word_for_own_and_cited_criteria(project, capsys, cited, values, outcome, word):
    """A rule met or missed reads meets or misses on a criterion of our own, and reproduced or not reproduced on a number
    someone else stated; the headline leads with that word."""
    int8_experiment(capsys)
    if cited:
        data = ok(capsys, "criterion", "add", "Ours: 1.43 validation loss on the held-out set", "-e", "e1", "--metric", "int8.val_loss", "--equals", "1.43",
                  "--tolerance", "0.05", "--source", "paper.txt", "--quote", "achieves 1.43 validation loss", "--locator", "Table 3", "--id", "c1")
        assert data["object"]["origin"] == "cited" and data["object"]["source"]["resolved"]["line"] == 2
    else:
        ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    for x in values:
        ok(capsys, "run", "add", "e1", f"int8.val_loss={x}")
    f = finding(capsys, "c1")
    assert f["rule_outcome"] == outcome and f["says"]["headline"].split(" · ")[0] == word and f["n"] == len(values)


def test_a_humans_criterion_met_by_a_later_run_on_a_clean_experiment_passes_every_gate(lab, capsys):
    """A human's criterion, declared before the run that meets it, on an experiment with nothing unknown or conflicting:
    the rule meets, the timing is before the evidence, integrity is ok, and no gate trips (unestablished included)."""
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    data = ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")
    [f] = data["findings"]
    assert (f["rule_outcome"], f["timing"], f["integrity"], f["says"]["integrity"]) == ("meets", "before", [], "ok")
    assert f["declared"]["by"] == HUMAN
    gate = ok(capsys, "status")["gate"]
    assert not any(gate.values()), gate


def test_a_cited_criterion_whose_file_does_not_state_the_target_is_refused(project, capsys):
    int8_experiment(capsys)
    refused(capsys, 2, "E_SOURCE_UNRESOLVED", "criterion", "add", "Ours: 1.5", "-e", "e1", "--metric", "int8.val_loss", "--equals", "1.5",
            "--tolerance", "0.05", "--source", "paper.txt", "--quote", "achieves 1.43 validation loss")


def test_a_cited_value_that_differs_from_ours_makes_the_cited_criterion_not_comparable(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "iters", "--source", "configs/eval.json#iters", "--cited", "32")
    ok(capsys, "criterion", "add", "Ours: 1.43", "-e", "e1", "--metric", "int8.val_loss", "--equals", "1.43", "--tolerance", "0.05",
       "--source", "paper.txt", "--quote", "achieves 1.43 validation loss", "--id", "c1")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.44")
    f = finding(capsys, "c1")     # 1.44 is within 1.43 ± 0.05, but under another iters it neither reproduces nor fails to
    assert f["rule_outcome"] == "undetermined" and f["says"]["rule"].startswith("criterion undetermined: not comparable")
    assert ("not_comparable", True) in [(c["code"], c["problem"]) for c in f["integrity"]]


# ---------------------------------------------------------------- runs


@needs_git
def test_a_run_records_its_numbers_and_a_receipt(lab, capsys):
    Path("README.md").write_text("x\n", encoding="utf-8")
    commit_all()
    int8_experiment(capsys)
    Path("out.txt").write_text("predictions\n", encoding="utf-8")
    run = ok(capsys, "run", "add", "e1", "fp32.val_loss=1.38", "int8.val_loss=1.42", "--command", "python eval.py --int8",
             "--commit", "abc1234", "--artifact", "out.txt", "--link", "wandb=https://wandb.ai/x/y")["object"]
    assert run["experiment"] == "e1" and run["metrics"] == {"fp32.val_loss": 1.38, "int8.val_loss": 1.42} and run["basis"] == "typed"
    assert re.fullmatch(r"run_[0-9A-Z]{26}", run["uid"]), run["uid"]
    r = run["receipt"]
    assert r["actor"] == HUMAN and r["command"] == "python eval.py --int8"
    assert r["produced"] == {"commit": "abc1234", "from": "flag"}
    assert r["attached"]["commit"] == git("rev-parse", "HEAD") and r["attached"]["branch"]
    assert run["files"][0]["path"] == "out.txt" and run["files"][0]["sha256"] == sha256_file(lab / "out.txt")
    assert run["links"] == {"wandb": "https://wandb.ai/x/y"}


@needs_git
def test_a_run_made_before_it_was_recorded_names_its_own_code_or_leaves_it_unknown(lab, capsys):
    """A run recorded as it lands, its file written a moment before, is recorded where and when it was made: its
    receipt's code and environment are the ones to run it again with. One made earlier, or at a time nobody knows, has
    the recorder's on its receipt: its environment is unknown, and so is its code without the commit it was made at,
    and rb says why; with the commit, its code is that commit's."""
    from datetime import datetime, timedelta, timezone
    Path("README.md").write_text("x\n", encoding="utf-8")
    commit_all()
    head = git("rev-parse", "HEAD")
    int8_experiment(capsys)

    def again() -> dict:
        return {r["what"]: (r["state"], r["detail"]) for r in ok(capsys, "show", "e1")["rerunnability"]}

    ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", "--produced-at", "2026-05-09T19:50:42Z")
    code, env = "the run was made before it was recorded, and names no commit", "the run was made before it was recorded, in an environment rb did not see"
    assert {k: again()[k] for k in ("code", "environment")} == {"code": ("unknown", code), "environment": ("unknown", env)}
    Path("metrics.json").write_text(json.dumps({"int8": {"val_loss": 1.5}}), encoding="utf-8")
    ok(capsys, "run", "add", "e1", "--from", "metrics.json")
    lately = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.55", "--produced-at", lately)
    rows = again()
    assert rows["code"][0] == "partial" and rows["code"][1].endswith(" · 1 of 3 runs was made before it was recorded and names no commit")
    assert rows["environment"][0] == "partial" and rows["environment"][1].endswith(" · 1 of 3 runs was made before it was recorded, in an environment rb did not see")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.6", "--produced-at", "unknown")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.7", "--produced-at", "2026-05-10", "--commit", head)
    state, detail = again()["code"]
    assert state == "partial" and detail.startswith(f"commit {head[:10]} · ")      # metrics.json is the uncommitted change kept
    assert detail.endswith(" · 2 of 5 runs were made before they were recorded and name no commit")
    state, detail = again()["environment"]       # a commit names the code, not the machine
    assert state == "partial" and detail.endswith(" · 3 of 5 runs were made before they were recorded, in environments rb did not see")


@needs_git
def test_counts_of_one_and_two_read_as_the_only_one_both_or_neither(project, capsys):
    """rb said 1 of 1 and 2 of 2 where the CHANGELOG promises a count of two reads as both and of one as the only one:
    in rb log's spec verify line ("9 of 9 checked"), in a folder's git line ("1 of 1 reports and results tracked") and
    in the rerun rows of two runs made before they were recorded ("none names its commit"). Each reads as the only one,
    both, all of them, or neither."""
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "batch_size", "6", "--source", "configs/train.yaml#batch_size")
    ok(capsys, "spec", "verify", "e1")
    assert main(["log", "-n", "1"]) == 0 and "the only setting checked as stated" in capsys.readouterr().out
    ok(capsys, "spec", "set", "e1", "amp", "false", "--source", "configs/train.yaml#amp")
    ok(capsys, "spec", "verify", "e1")
    assert main(["log", "-n", "1"]) == 0 and "both settings checked as stated" in capsys.readouterr().out
    for v in (1.4, 1.5):
        ok(capsys, "run", "add", "e1", f"int8.val_loss={v}", "--produced-at", "2026-05-09T19:50:42Z")
    rows = {r["what"]: (r["state"], r["detail"]) for r in ok(capsys, "show", "e1")["rerunnability"]}
    assert rows["code"] == ("unknown", "both runs were made before they were recorded, and neither names its commit"), rows
    assert rows["environment"] == ("unknown", "both runs were made before they were recorded, in environments rb did not see"), rows
    Path("results/calib").mkdir(parents=True)
    Path("results/calib/README.md").write_text("# Calibration\n", encoding="utf-8")
    commit_all("a report")
    assert main(["reconcile"]) == 0 and "git: the only report or results file is tracked · folder clean" in capsys.readouterr().out
    Path("results/calib/metrics.json").write_text('{"top1": 0.75}', encoding="utf-8")
    commit_all("its results")
    assert main(["reconcile"]) == 0 and "git: both report and results files are tracked · folder clean" in capsys.readouterr().out


@needs_git
def test_a_run_made_before_it_was_recorded_from_a_tree_says_its_uncommitted_changes(lab, capsys):
    """A run made earlier that names the exact code it ran from (--tree, as rb reconcile records a command rb journal run
    watched) has its code in that tree: one that is not its commit's says the run's uncommitted changes are kept."""
    Path("README.md").write_text("x\n", encoding="utf-8")
    commit_all()
    head = git("rev-parse", "HEAD")
    int8_experiment(capsys)
    Path("train.py").write_text("print(1)\n", encoding="utf-8")
    git("add", "train.py")
    tree = git("write-tree")
    git("reset", "-q", "train.py")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", "--produced-at", "2026-05-10", "--commit", head, "--tree", tree)
    code = {r["what"]: (r["state"], r["detail"]) for r in ok(capsys, "show", "e1")["rerunnability"]}["code"]
    assert code == ("ok", f"commit {head[:10]} · uncommitted changes kept as git trees")


@needs_git
def test_a_run_is_made_before_it_was_recorded_by_a_sign_of_its_own_output(lab, capsys, monkeypatch):
    """The bytes a run's numbers were read from say when it was made: the journal's first sighting of them, git's first
    commit of them or the file's own last write, whichever is earliest. The first commit of a path that held other
    bytes, or another file the run names, says nothing of it: one rerun into a committed path and recorded at once, one
    that keeps a file committed long before (--artifact), or one whose config the journal saw as it started, is recorded
    where and when it was made. Results committed long before, though checked out since, and results written long
    before and kept out of git, were made before they were recorded."""
    import os
    from datetime import datetime, timedelta, timezone
    Path("out").mkdir()
    for name, val_loss in (("a", 1.4), ("b", 1.5)):
        Path(f"out/{name}.json").write_text(json.dumps({"int8": {"val_loss": val_loss}}), encoding="utf-8")
    Path("train.yaml").write_text("lr: 2e-4\n", encoding="utf-8")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-05-01T10:00:00Z")
    commit_all()
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    int8_experiment(capsys)

    def again() -> dict:
        return {r["what"]: r["state"] for r in ok(capsys, "show", "e1")["rerunnability"]}

    Path("out/a.json").write_text(json.dumps({"int8": {"val_loss": 1.45}}), encoding="utf-8")
    run = ok(capsys, "run", "add", "e1", "--from", "out/a.json")["object"]
    assert run["produced_basis"] == "file" and run["receipt"]["produced"]["existed"]["by"] == "file"
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.46", "--artifact", "train.yaml")["object"]
    assert run["produced_basis"] == "git" and "existed" not in run["receipt"]["produced"]
    Path("out/r1").mkdir()
    Path("out/r1/config.yaml").write_text("lr: 2e-4\n", encoding="utf-8")
    monkeypatch.setenv("RB_TEST_NOW", (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat())
    ok(capsys, "journal", "scan")            # the run starts, and the journal sees its config
    monkeypatch.delenv("RB_TEST_NOW")
    Path("out/r1/metrics.json").write_text(json.dumps({"int8": {"val_loss": 1.48}}), encoding="utf-8")
    run = ok(capsys, "run", "add", "e1", "--from", "out/r1/metrics.json", "--artifact", "out/r1/config.yaml")["object"]
    assert run["produced_basis"] == "journal" and run["receipt"]["produced"]["existed"]["by"] == "file"
    assert {k: again()[k] for k in ("code", "environment")} == {"code": "ok", "environment": "ok"}
    os.utime("out/b.json")                                # checked out again since its bytes were committed
    run = ok(capsys, "run", "add", "e1", "--from", "out/b.json")["object"]
    assert run["produced_basis"] == "git" and run["receipt"]["produced"]["existed"] == {"at": "2026-05-01T10:00:00.999+00:00", "by": "git"}
    assert {k: again()[k] for k in ("code", "environment")} == {"code": "partial", "environment": "partial"}
    Path("out/c.json").write_text(json.dumps({"int8": {"val_loss": 1.47}}), encoding="utf-8")
    os.utime("out/c.json", (1767225600, 1767225600))      # written months ago, and kept out of git
    run = ok(capsys, "run", "add", "e1", "--from", "out/c.json")["object"]
    assert run["produced_basis"] == "file" and run["receipt"]["produced"]["existed"] == {"at": "2026-01-01T00:00:00.000+00:00", "by": "file"}
    rows = {r["what"]: r["detail"] for r in ok(capsys, "show", "e1")["rerunnability"]}
    assert rows["code"].endswith(" · 2 of 5 runs were made before they were recorded and name no commit")


@needs_git
def test_a_second_execution_that_wrote_the_same_bytes_is_recorded_as_it_landed(lab, capsys, monkeypatch):
    """A deterministic rerun writes byte-identical output, the check that holds, and rb asks for it with --again or
    --repeats. The journal and git saw those bytes for the run already recorded from them: they say when the output
    existed, not when the rerun was made. Recorded as soon as it ran, the rerun was made when its file was written, and
    its receipt's code and environment are its own."""
    int8_experiment(capsys)
    Path("results").mkdir()
    out = json.dumps({"int8": {"val_loss": 5.63}, "fp32": {"val_loss": 5.61}})
    Path("results/metrics.json").write_text(out, encoding="utf-8")
    first = ok(capsys, "run", "add", "e1", "--from", "results/metrics.json")["object"]["id"]
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-05-01T10:00:00Z")
    commit_all("results")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    Path("results/metrics.json").write_text(out, encoding="utf-8")         # the rerun after a refactor: the same bytes
    for extra in (("--again", "--why", "rerun after the refactor"), ("--repeats", first, "--again", "--why", "a check")):
        run = ok(capsys, "run", "add", "e1", "--from", "results/metrics.json", *extra)["object"]
        assert (run["produced_basis"], run["receipt"]["produced"]["existed"]["by"]) == ("git", "file"), extra
    rows = {r["what"]: r["state"] for r in ok(capsys, "show", "e1")["rerunnability"]}
    assert (rows["code"], rows["environment"]) == ("ok", "ok")


@needs_git
def test_output_recorded_again_existed_when_its_bytes_were_first_committed(lab, capsys, monkeypatch):
    """Recorded again (--again, as rb says to when the same file replaces a run), output git committed the day before a
    criterion was declared existed then: the criterion's timing reads it as evidence that came first, and the late gate
    fails, however recently a checkout wrote the file."""
    import os
    Path("results").mkdir()
    Path("results/m.json").write_text(json.dumps({"int8": {"val_loss": 5.63}, "fp32": {"val_loss": 5.61}}), encoding="utf-8")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-05-01T10:00:00Z")
    commit_all("results")
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 costs at most 0.05 nats", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")
    first = ok(capsys, "run", "add", "e1", "--from", "results/m.json")["object"]["id"]
    os.utime("results/m.json")                             # a fresh checkout writes the file again
    run = ok(capsys, "run", "add", "e1", "--from", "results/m.json", "--replaces", first, "--again", "--why", "the right command")["object"]
    assert (run["produced_at"], run["produced_basis"]) == ("2026-05-01T10:00:00.999+00:00", "git")
    assert finding(capsys, "c1")["timing"] == "after_evidence"
    assert rb(capsys, "status", "--fail-on", "late")[0] == 1


@needs_git
def test_a_file_git_never_committed_is_not_hashed_to_date_a_run(lab, capsys, tmp_path, monkeypatch):
    """A checkpoint kept out of git has no first commit, whatever its bytes: rb asks git whether a commit ever held the
    path before it has git hash the file, which for a large checkpoint takes seconds."""
    Path(".gitignore").write_text("ckpt/\n", encoding="utf-8")
    commit_all()
    int8_experiment(capsys)
    Path("ckpt").mkdir()
    Path("ckpt/model.pt").write_bytes(b"\0" * 4096)
    trace = tmp_path / "git-trace.txt"
    monkeypatch.setenv("GIT_TRACE", str(trace))
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", "--artifact", "ckpt/model.pt")
    asked = [line.split("trace: built-in: git ", 1)[1] for line in trace.read_text(encoding="utf-8").splitlines() if "trace: built-in: git " in line]
    assert not [a for a in asked if "hash-object" in a], asked


def test_a_run_variant_names_bare_numbers_for_that_variant(lab, capsys):
    int8_experiment(capsys)
    assert ok(capsys, "run", "add", "e1", "val_loss=1.2", "--variant", "int8")["object"]["metrics"] == {"int8.val_loss": 1.2}
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", "run", "add", "e1", "val_loss=1.2", "--variant", "int4")


def test_a_run_from_a_json_file_reads_nested_numbers_and_names_the_rest(lab, capsys):
    int8_experiment(capsys)
    Path("metrics.json").write_text(json.dumps({"int8": {"val_loss": 1.3, "top5_err": 5.1}, "model": "resnet50"}), encoding="utf-8")
    data = ok(capsys, "run", "add", "e1", "--from", "metrics.json")
    run = data["object"]
    assert run["metrics"] == {"int8.val_loss": 1.3, "int8.top5_err": 5.1} and run["basis"] == "file"
    assert run["files"][0]["path"] == "metrics.json" and run["files"][0]["sha256"] == sha256_file(lab / "metrics.json")
    assert any("model" in w for w in data["warnings"])


def test_an_identical_run_is_recognised_not_recorded_twice(lab, capsys):
    int8_experiment(capsys)
    first = ok(capsys, "run", "add", "e1", "int8.val_loss=1.4")["object"]["id"]
    data = ok(capsys, "run", "add", "e1", "int8.val_loss=1.4")
    assert data["duplicate_of"] == first
    assert len(list((lab / ".rb" / "experiments" / "e1" / "runs").glob("*.yaml"))) == 1
    assert [e["op"] for e in log_entries(lab)].count("record") == 1


def test_a_deliberate_repeat_needs_a_reason_and_is_marked(lab, capsys):
    int8_experiment(capsys)
    first = ok(capsys, "run", "add", "e1", "int8.val_loss=1.4")["object"]["id"]
    refused(capsys, 2, "E_OBJECT_INVALID", "run", "add", "e1", "int8.val_loss=1.4", "--again")
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", "--again", "-m", "rerun on another GPU")["object"]
    assert run["id"] != first and run["note"].startswith("recorded again: rerun on another GPU")


def test_run_add_names_as_unread_only_the_numbers_no_criterion_reads(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--alias", "cross_entropy")
    ok(capsys, "criterion", "add", "INT8 costs under 0.1 validation loss", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.1")
    data = ok(capsys, "run", "add", "e1", "fp32.cross_entropy=1.38", "int8.cross_entropy=1.42", "int8.latency_ms=25")
    status_says = [o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "unread_metric"]
    assert len(status_says) == 1 and "int8.latency_ms" in status_says[0] and "cross_entropy" not in status_says[0]
    assert data["unread_metrics"] == ["int8.latency_ms"]     # change.val_loss reads both variants' val_loss, by alias


def test_a_run_none_of_whose_numbers_a_criterion_reads_says_so_in_one_line(lab, capsys):
    """A flat metrics.json ({"val_loss": ...}) names no variant of a two-arm experiment, so the criterion on
    change.val_loss reads none of it: rb says so in one line, with the names its criteria read and the shape a file
    gives them in, and the SDK's run.log, which said nothing, says it as an RBWarning with the run.log that gives them.
    A run whose numbers a criterion reads says nothing of the kind."""
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05")
    nothing = lambda data: [w for w in data.get("warnings", []) if "counts toward nothing" in w]  # noqa: E731
    Path("out/seed1").mkdir(parents=True)
    Path("out/seed1/metrics.json").write_text(json.dumps({"val_loss": 0.987}), encoding="utf-8")
    assert nothing(ok(capsys, "run", "add", "e1", "--from", "out/seed1/metrics.json")) == [
        "e1: no criterion reads val_loss, so this run counts toward nothing. Its criteria read fp32.val_loss, int8.val_loss: a file gives "
        'them as {"fp32": {"val_loss": ...}, "int8": {"val_loss": ...}}, or --variant <name> takes one variant\'s numbers.']
    assert main(["run", "add", "e1", "val_loss=0.99"]) == 0
    err = capsys.readouterr().err
    assert err.count("counts toward nothing") == 1 and "a run gives them as fp32.val_loss=<value> int8.val_loss=<value>," in err
    Path("out/seed2").mkdir()
    Path("out/seed2/metrics.json").write_text(json.dumps({"fp32": {"val_loss": 0.961}, "int8": {"val_loss": 0.987}}), encoding="utf-8")
    assert nothing(ok(capsys, "run", "add", "e1", "--from", "out/seed2/metrics.json")) == []
    assert nothing(ok(capsys, "run", "add", "e1", "val_loss=0.97", "--variant", "int8")) == []
    # the SDK's run.log records through the same rules and says the same, as an RBWarning
    import warnings
    import rabbit_brain
    exp = rabbit_brain.open().experiment("e1")
    with warnings.catch_warnings(record=True) as told:
        warnings.simplefilter("always")
        with exp.run(per_run={}) as run:
            run.log({"val_loss": 0.975})
        with exp.run(variant="int8") as run:
            run.log(val_loss=0.976)
    assert [str(w.message) for w in told if issubclass(w.category, rabbit_brain.RBWarning) and "counts toward nothing" in str(w.message)] == [
        'e1: no criterion reads val_loss, so this run counts toward nothing. Its criteria read fp32.val_loss, int8.val_loss: a run logs '
        'them as run.log({"fp32.val_loss": ..., "int8.val_loss": ...}), or variant=<name> takes one variant\'s numbers.']


def test_a_run_whose_names_are_close_to_a_criterions_says_the_criterion_is_likely_misspelt(lab, capsys):
    """Declared before any run, the order rb asks for, a criterion on change.val_los drew no warning; the run add notice
    then told the agent to give the run's numbers as fp32.val_los=<value>, and rb status's Next was that line: rb steered
    the record onto the misspelling. The notice and rb status name the close name and how to declare it again."""
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 adds at most 0.05 loss", "-e", "e1", "--metric", "change.val_los", "--at-most", "0.05", "--id", "c1")
    data = ok(capsys, "run", "add", "e1", "fp32.val_loss=0.95", "int8.val_loss=0.97")
    assert [w for w in data["warnings"] if "counts toward nothing" in w] == ["e1: no criterion reads fp32.val_loss, int8.val_loss, so this run counts toward nothing. c1 reads val_los, a close "
                                'name to the val_loss this run gives: if it means val_loss, take it back (rb retract c1 --why "...") and declare it again on val_loss.']
    status = ok(capsys, "status")
    [item] = [i for i in status["open"] if i["subject"] == "c1" and i["code"] == "untested"]
    assert item["do"] == 'rb retract c1 --why "it reads val_los; the runs report val_loss"   (then declare it again on val_loss)'
    code, env = rb(capsys, "status")
    assert not any("val_los=" in n for n in env.next), env.next


def test_the_sdks_criterion_on_a_name_close_to_a_reported_one_warns_as_the_command_line_does(lab, capsys):
    """rb criterion add on change.val_los, beside runs that report val_loss, warned of the close name, and so did MCP's
    criterion_add; the SDK's criterion() recorded it with no word. It says the same line, as an RBWarning."""
    import warnings
    import rabbit_brain
    int8_experiment(capsys)
    ok(capsys, "run", "add", "e1", "fp32.val_loss=0.961", "int8.val_loss=0.987")
    with warnings.catch_warnings(record=True) as told:
        warnings.simplefilter("always")
        rabbit_brain.open().experiment("e1").criterion("INT8 adds at most 0.05 validation loss", metric="change.val_los", at_most=0.05, id="c-sdk")
    assert [str(w.message) for w in told if issubclass(w.category, rabbit_brain.RBWarning) and "close name" in str(w.message)] == [
        "no run reports val_los and the catalogue does not hold it; runs report val_loss, a close name. The criterion reads change.val_los as given."]


def test_a_criterion_on_a_name_close_to_a_reported_one_warns_once_and_stands(lab, capsys):
    """A criterion's --metric declares a name by use, so a misspelling is accepted; when no run or analysis reports
    the name and the catalogue does not hold it, and a name one of them does is close, rb names that name in one
    warning line. A name in use, one whose digits differ (top5 beside top1) and a far one draw no warning."""
    int8_experiment(capsys)
    ok(capsys, "run", "add", "e1", "fp32.val_loss=0.961", "int8.val_loss=0.987", "int8.top1=0.758")
    data = ok(capsys, "criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "e1", "--metric", "change.val_los", "--at-most", "0.05")
    assert data["object"]["metric"] == "change.val_los"
    assert data["warnings"] == ["no run reports val_los and the catalogue does not hold it; runs report val_loss, a close name. "
                                "The criterion reads change.val_los as given."]
    ok(capsys, "metric", "add", "latency_ms", "--minimize")
    data = ok(capsys, "criterion", "add", "INT8 stays fast", "-e", "e1", "--metric", "int8.latncy_ms", "--at-most", "30")
    assert data["warnings"] == ["no run reports latncy_ms and the catalogue does not hold it; the catalogue holds latency_ms, a close name. "
                                "The criterion reads int8.latncy_ms as given."]
    for metric in ("change.val_loss", "int8.top5", "int8.throughput"):
        assert "warnings" not in ok(capsys, "criterion", "add", "INT8 holds", "-e", "e1", "--metric", metric, "--at-most", "1"), metric


def test_a_number_no_criterion_reads_is_said_unread_with_no_rule_to_declare(lab, capsys):
    """Round 7: the unread_metric item offered `rb criterion add ... --at-most <x>`, which pulls toward a threshold
    nobody stated. It says the number is unread and suggests nothing, under Nothing to do; so does a comparison an
    analysis reports that no criterion reads."""
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", "int8.latency_ms=25")
    ok(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:latency_ms=-3[-4,-2]", "--id", "a1")
    items = {o["code"]: o for o in ok(capsys, "status")["open"]}
    assert items["unread_metric"]["what"] == "e1: runs report int8.latency_ms, which no recorded criterion reads" and items["unread_metric"]["do"] == ""
    assert items["unread_comparison"]["do"] == ""
    assert main(["status"]) in (0, 1)
    said = capsys.readouterr().out
    quiet = said[said.index("Nothing to do ("):]
    assert "  - e1: runs report int8.latency_ms, which no recorded criterion reads" in quiet
    assert "--at-most <x>" not in said and "--better" not in said
    # an item that asks for nothing offers no next step
    for command in ("status", "context"):
        assert main([command, "--json"]) in (0, 1)
        assert "" not in json.loads(capsys.readouterr().out)["next"], command


def test_every_number_and_comparison_no_criterion_reads_is_counted_however_many(lab, capsys):
    """Fourteen numbers no criterion reads still make the item, its first six by name and then how many more; five
    comparisons an analysis reports, its first three and how many more."""
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.4", *[f"int8.m{i:02d}={i}" for i in range(14)])
    [said] = [o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "unread_metric"]
    assert said == "e1: runs report int8.m00, int8.m01, int8.m02, int8.m03, int8.m04, int8.m05 and 8 more, which no recorded criterion reads"
    ok(capsys, "analysis", "add", "e1", *[x for m in "abcde" for x in ("--compare", f"int8/fp32:{m}=0.1[0.05,0.15]")], "--id", "a1")
    [said] = [o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "unread_comparison"]
    assert said.startswith("e1: a: int8 vs fp32 +0.1 [+0.05, +0.15] (a1); b: ") and said.endswith(
        "; c: int8 vs fp32 +0.1 [+0.05, +0.15] (a1); and 2 more: no recorded criterion reads them (one declared now reads as declared after its evidence)")


def test_every_list_cut_short_says_how_many_more(lab, capsys):
    """A list cut short says how many more: an analysis's comparisons on an experiment's card, a criterion's integrity
    problems on its card, what a record file fails on in rb doctor, the numbers a file holds that nothing declares, and
    the settings a run's config disagrees on. rb status says each item that names what is unread in its own words."""
    from rbtest import object_card
    int8_experiment(capsys)
    ok(capsys, "analysis", "add", "e1", *[x for m in "abcde" for x in ("--compare", f"int8/fp32:{m}=0.1[0.05,0.15]")], "--id", "a1")
    [said] = [f for f in object_card("e1").facts if f.startswith("analysis (")]
    assert said.endswith("; c int8 vs fp32 +0.1 [+0.05, +0.15]; and 2 more")
    for name in "abcdef":
        ok(capsys, "spec", "set", "e1", f"opt.{name}", "--unknown")
    ok(capsys, "criterion", "add", "c", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1", "--id", "c1")
    assert object_card("c1").looks[3:] == ["opt.d is unknown", "and 2 more"]
    Path("ran.yaml").write_text("opt:\n  a: 1\n  b: 1\n  c: 1\n  d: 1\n", encoding="utf-8")
    for name in "abcd":
        ok(capsys, "spec", "set", "e1", f"opt.{name}", "2", "--no-verify")
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=0.9", "--config", "ran.yaml")["object"]["id"]
    [o] = [o for o in ok(capsys, "show", "c1")["finding"]["observations"] if o["run"] == run]
    assert o["reason"].startswith("ran with opt.a 1 (the spec says 2); ") and o["reason"].endswith("; and 1 more")
    Path("out").mkdir()
    Path("out/m.json").write_text(json.dumps({k: i for i, k in enumerate(("alpha", "beta", "gamma", "delta", "eps"))}), encoding="utf-8")
    ok(capsys, "journal", "scan")
    assert "out/m.json holds numbers nothing declares (alpha, beta, delta and 2 more)" in ok(capsys, "reconcile")["unplaced"]
    for e in ("e2", "e3", "e4"):
        ok(capsys, "experiment", "add", "x", "--id", e, "--baseline", "fp32", "--candidate", "int8")
    for e in ("e1", "e2", "e3", "e4"):
        ok(capsys, "run", "add", e, *[f"int8.m{i}={i}" for i in range(8)])
    assert main(["status"]) in (0, 1)
    said = capsys.readouterr().out
    assert all(f"  - {e}: runs report int8.m0, int8.m1, int8.m2, int8.m3, int8.m4, int8.m5 and 2 more, which no recorded criterion reads" in said
               for e in ("e2", "e3", "e4"))
    ok(capsys, "metric", "add", "val_loss", "--minimize")
    Path(".rb/metrics/val_loss.yaml").write_text("rb: 1\nkind: metric\nid: val_loss\nunit: 5\ndirection: sideways\n", encoding="utf-8")
    [bad] = [c for c in rb(capsys, "doctor")[1].data["checks"] if c["check"] == "corrupt"]
    assert bad["detail"] == ".rb/metrics/val_loss.yaml is not a valid metric: created_by: Field required; created_at: Field required; and 2 more"


@needs_git
def test_rb_show_says_a_repository_with_no_commit_yet_and_counts_an_analysis_s_units(lab, capsys):
    """A run recorded before the first commit says so in full, and an analysis says how many of its units in the
    plural, as the criterion that reads it does, a unit given in the plural as given."""
    int8_experiment(capsys)
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.4")["object"]["id"]
    assert main(["show", run]) == 0 and "repository when recorded: no commit yet\n" in capsys.readouterr().out
    a = ok(capsys, "analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.1[0.05,0.15]", "--method", "bootstrap", "--unit", "image",
           "--units", "100")["object"]["id"]
    assert main(["show", a]) == 0
    said = capsys.readouterr().out
    assert "method: bootstrap · 100 images · 95%" in said and "· 100 images" in said
    assert main(["analysis", "add", "e1", "--compare", "int8/fp32:val_loss=0.2[0.1,0.3]", "--method", "bootstrap", "--unit", "images",
                 "--units", "200"]) == 0
    said = capsys.readouterr().out
    assert "· bootstrap · 200 images" in said and "imageses" not in said        # given in the plural, said as given


def test_a_call_made_on_readings_that_moved_counts_every_one(lab, capsys):
    """A call on an experiment or a question made on six readings that have all moved since names the first four and
    says how many more, in the call waiting on a human and in rb show."""
    start(capsys)
    ok(capsys, "question", "add", "Does INT8 hold?", "--id", "q1")
    ok(capsys, "experiment", "add", "INT8 vs FP32", "--id", "e1", "-q", "q1", "--baseline", "fp32", "--candidate", "int8")
    for i in range(1, 7):
        ok(capsys, "criterion", "add", f"m{i}", "-e", "e1", "--metric", f"int8.m{i}", "--at-most", "10", "--id", f"c{i}", "-q", "q1")
    ok(capsys, "run", "add", "e1", *[f"int8.m{i}=1" for i in range(1, 7)])
    for subject in ("q1", "e1"):
        ok(capsys, "decide", subject, "continue", "--why", "go on")
    ok(capsys, "run", "add", "e1", *[f"int8.m{i}=2" for i in range(1, 7)])
    moved = [o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "decision_outdated"]
    assert len(moved) == 2 and all(m.endswith("was made on readings that have moved since: c1, c2, c3, c4 and 2 more") for m in moved)
    assert main(["show", "e1"]) == 0
    assert "made on readings that have moved since (c1, c2, c3, c4 and 2 more)" in capsys.readouterr().out


def test_run_numbers_are_name_equals_number(lab, capsys):
    int8_experiment(capsys)
    refused(capsys, 2, "E_USAGE", "run", "add", "e1", "val_loss")
    refused(capsys, 2, "E_OBJECT_INVALID", "run", "add", "e1", "val_loss=abc")
    refused(capsys, 2, "E_OBJECT_INVALID", "run", "add", "e1")


# ---------------------------------------------------------------- people's calls, and agents


def _agent_setup(capsys, monkeypatch):
    """A human's e1 with criterion c1 and a run it counts; then the agent's session. Returns the run's id."""
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")["object"]["id"]
    monkeypatch.setenv("CLAUDECODE", "1")
    return run


@pytest.mark.parametrize("argv, by", [
    (["freeze", "e1", "-m", "pre-register"], lambda capsys, run: ok(capsys, "show", "e1")["object"]["frozen"]["by"]),
    # a call the agent records is the agent's, and proposed: the finding reads no review until the user confirms it
    (["decide", "c1", "accept", "-m", "looks fine"], lambda capsys, run: ok(capsys, "status")["pending"][0]["by"] if finding(capsys, "c1")["review"] is None else None),
    (["retract", "{run}", "-m", "wrong normalisation"], lambda capsys, run: ok(capsys, "show", run)["object"]["retracted"]["by"]),
], ids=["freeze", "decide", "retract"])
def test_an_agents_freeze_ruling_and_retraction_are_recorded_as_the_agents(lab, capsys, monkeypatch, argv, by):
    """Nothing is refused for who makes it: the call goes through, the record it makes names the agent, and so does
    every log line it writes."""
    run = _agent_setup(capsys, monkeypatch)
    argv = [a.format(run=run) for a in argv]
    before = len(log_entries(lab))
    data = ok(capsys, *argv)
    assert data["actor"] == {"id": "agent:claude-code", "via": "detected:CLAUDECODE",
                             "agent": {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}}
    assert by(capsys, run) == "agent:claude-code"
    written = log_entries(lab)[before:]
    assert written and all(e["actor"] == "agent:claude-code" for e in written)


def test_an_agents_writes_are_recorded_as_the_agent(lab, capsys, monkeypatch):
    int8_experiment(capsys)
    monkeypatch.setenv("CLAUDECODE", "1")
    data = ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")
    assert data["actor"] == {"id": "agent:claude-code", "via": "detected:CLAUDECODE",
                             "agent": {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}}
    r = data["object"]["receipt"]
    assert r["actor"] == "agent:claude-code" and r["actor_via"] == "detected:CLAUDECODE"
    assert log_entries(lab)[-1]["actor"] == "agent:claude-code"


def test_a_human_named_inside_an_agent_session_is_still_the_agent(lab, capsys, monkeypatch):
    """A human's name typed inside an agent session is what an agent would type: rb ignores it and records the agent,
    so the ruling is the agent's, on the record, and proposed, so the finding reads no review; the name is kept in the
    log only as ignored."""
    _agent_setup(capsys, monkeypatch)
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    data = ok(capsys, "decide", "c1", "accept", "-m", "within budget")
    assert data["actor"] == {"id": "agent:claude-code", "via": "detected:CLAUDECODE", "ignored_rb_actor": "human:ana",
                             "agent": {"name": "claude-code", "provider": "anthropic", "provider_from": "inferred"}}
    assert data["object"]["by"] == "agent:claude-code" and data["authority"]["state"] == "proposed"
    assert finding(capsys, "c1")["review"] is None
    last = log_entries(lab)[-1]
    assert (last["op"], last["actor"], last["ignored_rb_actor"]) == ("decide", "agent:claude-code", "human:ana")


def test_a_frozen_spec_changes_only_with_an_amendment(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    frozen = ok(capsys, "freeze", "e1", "-m", "pre-register")["object"]["frozen"]
    assert frozen["by"] == HUMAN and frozen["why"] == "pre-register"
    refused(capsys, 2, "E_FROZEN", "spec", "set", "e1", "lr", "0.2")
    refused(capsys, 2, "E_FROZEN", "criterion", "add", "late", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1")
    refused(capsys, 2, "E_OBJECT_INVALID", "spec", "set", "e1", "lr", "0.2", "--amend")
    ok(capsys, "spec", "set", "e1", "lr", "0.2", "--amend", "-m", "the config was fixed before any held-out run")
    [amendment] = ok(capsys, "show", "e1")["object"]["amendments"]
    assert amendment["why"] == "the config was fixed before any held-out run" and "0.1 -> 0.2" in amendment["change"]


@pytest.mark.parametrize("kind, outcome, state", [("experiment", "reject", "rejected"), ("experiment", "accept", "accepted"),
                                                  ("question", "accept", "answered"), ("question", "reject", "dropped")])
def test_a_decision_moves_the_state_of_what_it_decides(lab, capsys, kind, outcome, state):
    """A question's state is read from the decisions on it, never stored on the question."""
    start(capsys)
    subject = ok(capsys, kind, "add", "something")["object"]["id"]
    d = ok(capsys, "decide", subject, outcome, "-m", "because")["object"]
    assert d["kind"] == "decision" and d["subject"] == subject and d["action"] == outcome and d["by"] == HUMAN
    if kind == "experiment":
        assert ok(capsys, "show", subject)["state"] == state
    else:
        assert "status" not in ok(capsys, "show", subject)["object"]
        assert [q["state"] for q in ok(capsys, "status")["questions"] if q["id"] == subject] == [state]


def test_a_humans_accept_on_a_setting_vouches_for_it(lab, capsys):
    """A setting nobody checked is a note on the finding, not an integrity problem, and status asks for its source; a
    human's accept vouches for the value it has: the note says vouched, and status stops asking."""
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")
    f = finding(capsys, "c1")
    assert f["integrity"] == [] and ("provisional", "e1/lr", False) in [(c["code"], c["subject"], c["problem"]) for c in f["caveats"]]
    assert ("provisional", "e1/lr") in [(o["code"], o["subject"]) for o in ok(capsys, "status")["open"]]
    d = ok(capsys, "decide", "e1/lr", "accept", "-m", "read it in the launch script myself")["object"]
    assert d["subject"] == "e1/lr" and d["value"] == 0.1
    f = finding(capsys, "c1")
    notes = [(c["code"], c["subject"], c["problem"]) for c in f["caveats"]]
    assert ("vouched", "e1/lr", False) in notes and not [n for n in notes if n[0] == "provisional"] and f["integrity"] == []
    assert "provisional" not in [o["code"] for o in ok(capsys, "status")["open"]]


def test_retracting_a_run_keeps_it_on_record_and_stops_it_counting(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")["object"]["id"]
    refused(capsys, 2, "E_USAGE", "retract", run)
    data = ok(capsys, "retract", run, "-m", "wrong input normalisation")
    assert data["object"] == {"kind": "run", "id": run, "retracted": {"why": "wrong input normalisation", "scope": []}}
    assert [ch["criterion_id"] for ch in data["finding_changes"]] == ["c1"]
    kept = rbyaml.loads((lab / ".rb" / "experiments" / "e1" / "runs" / f"{run}.yaml").read_text(encoding="utf-8"))
    assert kept["retracted"]["why"] == "wrong input normalisation" and kept["retracted"]["by"] == HUMAN
    f = finding(capsys, "c1")
    assert (f["rule_outcome"], f["result"], f["n"], f["observations"]) == ("untested", "untested", 0, [])


def test_a_setting_taken_back_and_set_again_stays_on_record_beside_the_new_one(lab, capsys):
    """A shared setting retracted and then set again under its name, as a variant's own can be: the experiment keeps
    the one taken back beside the new one, and every command reads it, the SDK and rb doctor included. Set again while
    the new one stands, that one changes in place."""
    import rabbit_brain
    int8_experiment(capsys)
    for name in ("lr", "int8.lr"):
        ok(capsys, "spec", "set", "e1", name, "0.1", "--source", "note:a guess")
        ok(capsys, "retract", f"e1/{name}", "--why", "the wrong run's value")
        ok(capsys, "spec", "set", "e1", name, "0.2", "--source", "note:a second guess")
        ok(capsys, "spec", "set", "e1", name, "0.3", "--source", "note:the value it ran with")
        assert ok(capsys, "show", f"e1/{name}")["object"]["value"] == 0.3
    kept = rbyaml.loads((lab / ".rb" / "experiments" / "e1" / "experiment.yaml").read_text(encoding="utf-8"))["settings"]
    assert [(s["name"], s["value"], "retracted" in s) for s in kept] == [("lr", 0.1, True), ("lr", 0.3, False)]
    assert rb(capsys, "status")[0] == 0 and rb(capsys, "doctor")[0] == 0
    assert [(s.name, s.value, s.retracted is None) for s in rabbit_brain.open().experiment("e1").data.settings] == [("lr", 0.1, False), ("lr", 0.3, True)]


# ---------------------------------------------------------------- status and its gates


def _gate_state(capsys):
    """c1 misses and nobody has decided on it, c2 untested, c3 a cited number not reproduced, seed unknown (an integrity
    problem for every finding); no verified source, and every criterion declared before the run."""
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "seed", "--unknown")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.0", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.0", "--id", "c1")
    ok(capsys, "criterion", "add", "fast", "-e", "e1", "--metric", "int8.latency_ms", "--at-most", "10", "--id", "c2")
    ok(capsys, "criterion", "add", "Ours: 1.43", "-e", "e1", "--metric", "int8.val_loss", "--equals", "1.43", "--tolerance", "0.01",
       "--source", "paper.txt", "--quote", "achieves 1.43 validation loss", "--id", "c3")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.5")


@pytest.mark.parametrize("gate, exit_code", [("unestablished", 1), ("untested", 1), ("unmet", 1),
                                             ("undecided", 1), ("unknown", 1), ("integrity", 1), ("stale", 0), ("late", 0)])
def test_status_fail_on_exits_1_only_for_a_gate_that_trips(project, capsys, gate, exit_code):
    _gate_state(capsys)
    code, env = rb(capsys, "status", "--fail-on", gate)
    assert code == exit_code and env.ok and env.data["outcome"]["passed"] == (exit_code == 0)
    assert env.data["gate"][gate] == (exit_code == 1)


def test_status_fail_on_takes_several_gates_and_refuses_an_unknown_one(project, capsys):
    _gate_state(capsys)
    code, env = rb(capsys, "status", "--fail-on", "stale,unmet")
    assert code == 1 and [f["code"] for f in env.data["outcome"]["failures"]] == ["unmet"]
    refused(capsys, 2, "E_OBJECT_INVALID", "status", "--fail-on", "bogus")


def test_status_without_fail_on_reports_and_exits_0(project, capsys):
    _gate_state(capsys)
    st = ok(capsys, "status")
    assert st["counts"]["criterion_count"] == 3 and st["counts"]["experiment_count"] == 1 and st["counts"]["run_count"] == 1
    assert st["tally"] == {"misses": 1, "untested": 1, "not reproduced": 1}
    assert st["results"] == {"point_only": 2, "untested": 1}
    assert ("human", "undecided", "c1") in [(o["who"], o["code"], o["subject"]) for o in st["open"]]
    assert st["actor"]["id"] == HUMAN


def test_a_cited_number_not_reproduced_is_said_so_wherever_a_call_waits_on_it_or_was_made_on_it(project, capsys):
    """A cited criterion that misses is not reproduced, never a rule missed: in the call waiting on it, in a call that
    accepts it against its rule and what that call was made on, and once its reading moves."""
    _gate_state(capsys)
    waiting = {o["subject"]: o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "undecided"}
    assert waiting["c1"].startswith("c1 misses its rule and no call on it is recorded: ")
    assert waiting["c3"] == "c3 is not reproduced and no call on it is recorded: Ours: 1.43"
    assert main(["decide", "c3", "accept", "--why", "close enough"]) == 0
    said = capsys.readouterr().out
    assert "made on c3: 1.5, n 1 (rb): point only, not reproduced" in said
    assert "This goes against the rule (not reproduced)." in said and "misses" not in said
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.6")
    [moved] = [o["what"] for o in ok(capsys, "status")["open"] if o["code"] == "decision_outdated" and o["subject"] == "c3"]
    assert "was made on 1.5, n 1 (rb): point only, not reproduced; c3 now reads" in moved, moved
    assert "made on an earlier reading (1.5, n 1 (rb): point only, not reproduced)" in finding(capsys, "c3")["says"]["review"]


def test_status_fail_on_stale_trips_when_a_verified_source_changes(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    assert rb(capsys, "status", "--fail-on", "stale")[0] == 0
    Path("configs/train.yaml").write_text(TRAIN_YAML.replace("1e-4", "3e-4"), encoding="utf-8")
    code, env = rb(capsys, "status", "--fail-on", "stale")
    assert code == 1 and ("conflict", "e1/lr") in [(o["code"], o["subject"]) for o in env.data["open"]]


def test_status_counts_a_setting_whose_source_says_otherwise_as_conflict_or_stale(project, capsys):
    """As this read finds a verified source, and as a conflict on record once re-verifying keeps it there."""
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    ok(capsys, "spec", "set", "e1", "batch_size", "--source", "configs/train.yaml#batch_size")
    [e] = ok(capsys, "status")["experiments"]
    assert e["settings_count"] == {"verified": 2, "provisional": 0, "unknown": 0, "inherited": 0, "per_run": 0}
    assert main(["status"]) == 0
    assert "· settings 2 verified · 0 provisional · 0 unknown · runs 0" in capsys.readouterr().out
    Path("configs/train.yaml").write_text(TRAIN_YAML.replace("lr: 1e-4", "lr: 3e-4").replace("batch_size: 6\n", ""), encoding="utf-8")
    [e] = ok(capsys, "status")["experiments"]
    assert e["settings_count"] == {"verified": 0, "provisional": 0, "unknown": 0, "inherited": 0, "per_run": 0, "conflict": 1, "stale": 1}
    assert main(["status"]) == 0
    assert "· settings 0 verified · 0 provisional · 0 unknown · 1 conflict · 1 stale · runs 0" in capsys.readouterr().out
    assert rb(capsys, "spec", "verify", "e1")[0] == 1          # lr's conflict is now on record; batch_size is provisional
    [e] = ok(capsys, "status")["experiments"]
    assert e["settings_count"] == {"verified": 0, "provisional": 1, "unknown": 0, "inherited": 0, "per_run": 0, "conflict": 1}
    assert main(["status"]) == 0
    assert "· settings 0 verified · 1 provisional · 0 unknown · 1 conflict · runs 0" in capsys.readouterr().out


def test_status_for_one_experiment_shows_only_its_criteria(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "experiment", "add", "other", "--id", "e2")
    ok(capsys, "criterion", "add", "on e1", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1", "--id", "c1")
    ok(capsys, "criterion", "add", "on e2", "-e", "e2", "--metric", "val_loss", "--at-most", "1", "--id", "c2")
    st = ok(capsys, "status", "e1")
    assert st["experiment_id"] == "e1" and [f["criterion"] for f in st["findings"]] == ["c1"] and [e["id"] for e in st["experiments"]] == ["e1"]


# ---------------------------------------------------------------- compare, show, log, context, schema


def test_compare_gives_means_and_changes_against_the_baseline_in_each_metrics_direction(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--unit", "nats", "--minimize")
    ok(capsys, "metric", "add", "acc", "--maximize")
    ok(capsys, "run", "add", "e1", "fp32.val_loss=1.30", "int8.val_loss=1.50", "fp32.acc=0.90", "int8.acc=0.95", "gpu_hours=3")
    ok(capsys, "run", "add", "e1", "fp32.val_loss=1.34", "int8.val_loss=1.54")
    t = ok(capsys, "compare", "e1")
    assert t["baseline"] == "fp32" and t["run_count"] == 2
    rows = {r["variant"]: r["metrics"] for r in t["rows"]}
    # a grid's cell fields beside them (runs, run_ids, count_said) are additive
    assert {k: rows["fp32"]["val_loss"][k] for k in ("mean", "n", "runs", "count_said")} == {"mean": pytest.approx(1.32), "n": 2, "runs": 2, "count_said": "2 runs"}
    assert set(rows["fp32"]["val_loss"]) == {"mean", "n", "runs", "run_ids", "count_said"}
    assert rows["int8"]["val_loss"]["mean"] == pytest.approx(1.52) and rows["int8"]["val_loss"]["delta"] == pytest.approx(0.2)
    assert rows["int8"]["val_loss"]["better"] is False            # a higher validation loss is worse
    assert rows["int8"]["acc"]["delta"] == pytest.approx(0.05) and rows["int8"]["acc"]["better"] is True
    assert t["overall"] == {"gpu_hours": {"mean": 3.0, "n": 1}}
    assert {m["name"]: m["unit"] for m in t["metrics"]} == {"val_loss": "nats", "acc": ""}


def test_one_comparison_shows_one_number_where_a_criterion_reads_it_paired_over_runs_it_selects(lab, capsys):
    """The Oct 11 trials' pages showed int8 at +0.917 against fp32, its runs at two depths mixed, beside the +0.928
    a criterion read paired at depth 10: the same comparison with two numbers. A setting a criterion selects runs by
    is a condition, so the table holds a row per variant and depth, and its change at depth 10 is the number c10
    reads: rb compare and the page alike, never the +0.125 pooled across depths."""
    import html as html_mod
    import re
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--unit", "nats", "--minimize")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "spec", "set", "e1", "depth", "--per-run")
    for seed in (1, 2, 3):
        for depth, fp32, int8 in ((6, 1.30, 1.31), (10, 1.20, 1.40)):
            ok(capsys, "run", "add", "e1", f"fp32.val_loss={fp32 + seed / 100}", f"int8.val_loss={int8 + seed / 50}", "--set", f"seed={seed}",
               "--set", f"depth={depth}", "--command", "python eval.py")
    ok(capsys, "criterion", "add", "At depth 10 int8 costs at most 0.3 nats", "-e", "e1", "--metric", "val_loss", "--subject", "int8",
       "--reference", "fp32", "--where", "depth=10", "--at-most", "0.3", "--role", "secondary", "--id", "c10")
    t = ok(capsys, "compare", "e1")
    assert t["conditions"] == ["depth"]
    cells = {(r["variant"], r["where"]["depth"]): r["metrics"]["val_loss"] for r in t["rows"]}
    assert list(cells) == [("fp32", 6), ("fp32", 10), ("int8", 6), ("int8", 10)]
    reads = ok(capsys, "show", "c10")["finding"]["comparison"]
    assert reads["estimate"] == pytest.approx(0.22) and cells[("int8", 10)]["delta"] == pytest.approx(0.22)
    assert "read_by" not in cells[("int8", 10)] and cells[("int8", 6)]["delta"] == pytest.approx(0.03)
    assert all(c["n"] == 3 for c in cells.values())
    assert main(["compare", "e1"]) == 0
    said = capsys.readouterr().out
    assert "+0.22 worse" in said and "+0.125" not in said
    assert main(["page", "--out", "page.html"]) == 0
    capsys.readouterr()
    raw = Path("page.html").read_text(encoding="utf-8")
    page = " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
    table = page[page.index("Comparison"):page.index("Its rules")]
    # the grid (PIVOT): two variants under two depths lie on their side, a row per depth, the change within it
    assert '<thead><tr><th></th><th>FP32 <span class="tag">baseline</span></th><th>INT8</th><th>Difference</th></tr></thead>' in raw
    # what it varies, as rb show says it: once, beside what it compares (rb gives compares), never again as a facts row
    card = raw.split('id="x-e1"', 1)[1].split("</article>", 1)[0]
    assert re.search(r'<p class="cmpl"><span>[^<]+</span><span> · varies </span><span class="mono">precision</span></p>', card)
    assert "<dt>varies</dt>" not in card
    assert re.search(r"Depth: 10 1\.22 · 3 \w+ 1\.44 · 3 \w+ 0\.22 nats higher than FP32", table) and "0.125" not in table, table


def test_a_variant_run_in_two_cells_has_a_row_per_cell_never_a_mean_pooled_across_them(lab, capsys):
    """Round 9: the cold trial's page showed p6 at one mean over n=6, its runs at two loop counts pooled, and p4 over
    four runs at both. A design factor is a condition as a criterion's selector is: a row per variant and cell, the
    change against the baseline's same cell, the SDK's table the same. The unit is no condition, and a table whose
    runs take one cell keeps one row per variant."""
    import html as html_mod
    import re
    import rabbit_brain
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "top1", "--maximize")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "spec", "set", "e1", "population.unit", "seed")
    ok(capsys, "spec", "set", "e1", "calib", "--per-run", "--design-factor")
    for seed in (1, 2, 3):
        for calib, top1 in ((512, 0.752), (1024, 0.761)):
            ok(capsys, "run", "add", "e1", f"fp32.top1={top1 + seed / 1000}", "--set", f"seed={seed}", "--set", f"calib={calib}")
        ok(capsys, "run", "add", "e1", f"int8.top1={0.755 + seed / 1000}", "--set", f"seed={seed}", "--set", "calib=1024")
    ok(capsys, "run", "add", "e1", "int8.top1=0.740", "--set", "seed=1", "--set", "calib=512")
    ok(capsys, "criterion", "add", "The claim that INT8 keeps top-1 within 0.01 at calib 1024 is checked here", "-e", "e1", "--metric", "top1",
       "--subject", "int8", "--reference", "fp32", "--where", "seed=1", "--at-least", "-0.01", "--id", "c1")
    t = ok(capsys, "compare", "e1")
    assert t["conditions"] == ["calib"]
    cells = {(r["variant"], r["where"]["calib"]): r["metrics"]["top1"] for r in t["rows"]}
    assert list(cells) == [("fp32", 512), ("fp32", 1024), ("int8", 512), ("int8", 1024)]
    assert cells[("fp32", 512)]["mean"] == pytest.approx(0.754) and cells[("fp32", 1024)]["mean"] == pytest.approx(0.763)
    keys = ("mean", "n", "delta", "better", "paired", "basis", "primary")
    assert {k: cells[("int8", 512)][k] for k in keys} == {"mean": pytest.approx(0.740), "n": 1, "delta": pytest.approx(-0.014), "better": False,
                                                          "paired": False, "basis": "n=1, unpaired", "primary": False}
    # the grid's: only seed 1 of fp32's three meets int8's, so no interval over all of them
    assert cells[("int8", 512)]["no_interval"] == "only 1 of the seeds match, so no interval over all of them" and "lo" not in cells[("int8", 512)]
    assert cells[("int8", 1024)]["mean"] == pytest.approx(0.757) and cells[("int8", 1024)]["delta"] == pytest.approx(-0.006)
    assert rabbit_brain.open().experiment("e1").compare()["rows"] == t["rows"]
    assert main(["compare", "e1"]) == 0
    said = capsys.readouterr().out
    assert "calib" in said.splitlines()[1] and "0.7528" not in said and "(n=4)" not in said
    assert main(["page", "--out", "page.html"]) == 0
    capsys.readouterr()
    page = " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8"))).split())
    # a row per cell (the grid on its side, PIVOT), its condition named on the row (Calib: 512), the change within it
    assert re.search(r"Calib: 512 0\.754 · 3 \w+ 0\.740 · 1 run 0\.014 lower than FP32 · [^.]*so no interval", page), page
    # one cell: no condition and one row per variant, as before
    ok(capsys, "experiment", "add", "INT8 at one size", "--id", "e2", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "spec", "set", "e2", "calib", "--per-run", "--design-factor")
    for seed in (1, 2):
        ok(capsys, "run", "add", "e2", f"fp32.top1={0.761 + seed / 1000}", f"int8.top1={0.755 + seed / 1000}", "--set", "calib=1024")
    t2 = ok(capsys, "compare", "e2")
    assert "conditions" not in t2 and [r["variant"] for r in t2["rows"]] == ["fp32", "int8"] and all("where" not in r for r in t2["rows"])


def test_a_change_vs_baseline_says_one_run_unpaired_units_and_a_second_factor(lab, capsys):
    """Round 10: the cold trial's page printed '+0.743 worse vs baseline' for one run against a three-seed mean,
    unpaired and with no word of it, and with a variant per loop count a candidate at another loop count read against
    the baseline as if the one factor moved. A change says what it rests on, on rb compare, the page and rb open: n=1,
    unpaired where the baseline's units are not the same, and the factors it differs in where more than one; a change
    of the same units in one factor says nothing more. The JSON adds `paired`, `basis` and `differs_in`."""
    import html as html_mod
    import re
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "top1", "--maximize")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "spec", "set", "e1", "population.unit", "seed")
    ok(capsys, "spec", "set", "e1", "calib", "--per-run", "--design-factor")
    for seed in (1, 2, 3):
        for calib in (512, 1024):
            ok(capsys, "run", "add", "e1", f"fp32.top1={0.761 + seed / 1000}", f"int8.top1={0.755 + seed / 1000 - (0.01 if calib == 512 else 0)}",
               "--set", f"seed={seed}", "--set", f"calib={calib}")
    t = ok(capsys, "compare", "e1")
    same = {(r["variant"], r["where"]["calib"]): r["metrics"]["top1"] for r in t["rows"]}
    assert same[("int8", 1024)]["paired"] is True and "basis" not in same[("int8", 1024)] and "differs_in" not in same[("int8", 1024)]
    # one more int8 run, with a seed the baseline has no run of: the units differ
    ok(capsys, "run", "add", "e1", "int8.top1=0.740", "--set", "seed=4", "--set", "calib=512")
    t = ok(capsys, "compare", "e1")
    cells = {(r["variant"], r["where"]["calib"]): r["metrics"]["top1"] for r in t["rows"]}
    assert cells[("int8", 512)]["paired"] is False and cells[("int8", 512)]["basis"] == "unpaired"
    ok(capsys, "experiment", "add", "INT8 at one run", "--id", "e3", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "spec", "set", "e3", "seed", "--per-run")
    ok(capsys, "spec", "set", "e3", "population.unit", "seed")
    for seed in (1, 2, 3):
        ok(capsys, "run", "add", "e3", f"fp32.top1={0.761 + seed / 1000}", "--set", f"seed={seed}")
    ok(capsys, "run", "add", "e3", "int8.top1=0.755", "--set", "seed=1")
    cell = {r["variant"]: r["metrics"]["top1"] for r in ok(capsys, "compare", "e3")["rows"]}["int8"]
    assert cell["n"] == 1 and cell["paired"] is False and cell["basis"] == "n=1, unpaired"
    assert main(["compare", "e3"]) == 0
    said = capsys.readouterr().out
    assert "-0.008 worse (n=1, unpaired)" in said, said
    # a variant per value of two factors: a candidate that differs from the baseline in both is no one factor's change
    ok(capsys, "experiment", "add", "INT8 by calibration size", "--id", "e4", "--baseline", "fp32c1024", "--candidate", "int8c512",
       "--candidate", "fp32c512", "--varies", "precision", "--varies", "calib")
    for v, precision, calib in (("fp32c1024", "fp32", 1024), ("int8c512", "int8", 512), ("fp32c512", "fp32", 512)):
        ok(capsys, "spec", "set", "e4", f"{v}.precision", precision, "--no-verify")
        ok(capsys, "spec", "set", "e4", f"{v}.calib", str(calib), "--no-verify")
    ok(capsys, "run", "add", "e4", "fp32c1024.top1=0.762", "int8c512.top1=0.745", "fp32c512.top1=0.760")
    cells = {r["variant"]: r["metrics"]["top1"] for r in ok(capsys, "compare", "e4")["rows"]}
    assert cells["int8c512"]["differs_in"] == ["precision", "calib"] and cells["int8c512"]["paired"] is True
    assert cells["int8c512"]["basis"] == "n=1; differs from the baseline in precision and calib"
    assert "differs_in" not in cells["fp32c512"] and cells["fp32c512"]["basis"] == "n=1"
    assert main(["compare", "e4"]) == 0
    said = capsys.readouterr().out
    assert "-0.017 (n=1; differs from the baseline in precision and calib)" in said and "-0.017 worse" not in said, said
    assert main(["page", "--out", "page.html"]) == 0
    capsys.readouterr()
    page = " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8"))).split())
    assert re.search(r"INT8 0\.7550 · 1 run 0\.0080 lower than FP32 · [^.]*so no interval", page), page
    assert re.search(r"INT8 · Calib: 512 0\.745 · 1 run 0\.017 lower than the baseline · [^·]*so no interval · also differs in precision and calib", page), page
    assert re.search(r"FP32 · Calib: 512 0\.760 · 1 run 0\.0020 lower than the baseline · [^·]*so no interval", page), page


def test_a_per_run_setting_a_variant_holds_two_values_of_splits_the_table_and_the_reading_says_it_pools(lab, capsys):
    """Review of round 9's fix: with calib only --per-run (no design factor, no --where), int8 ran at 512 and 1024 under
    one unit each, and the table showed one int8 mean over both while the rule read them as one arm with no word of it.
    With the unit declared, such a setting is a condition: a row per cell; the reading that pools it says so."""
    import rabbit_brain
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "top1", "--maximize")
    for name in ("calib", "seed"):
        ok(capsys, "spec", "set", "e1", name, "--per-run")
    ok(capsys, "spec", "set", "e1", "population.unit", "seed")
    ok(capsys, "criterion", "add", "INT8 against FP32", "-e", "e1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
       "--better", "--id", "c1")
    for seed in (1, 2, 3):
        for calib, top1 in ((512, 0.750), (1024, 0.760)):
            ok(capsys, "run", "add", "e1", "fp32.top1=0.761", f"int8.top1={top1 + seed / 1000}", "--set", f"seed={seed}", "--set", f"calib={calib}")
    t = ok(capsys, "compare", "e1")
    assert t["conditions"] == ["calib"]
    cells = {(r["variant"], r["where"]["calib"]): r["metrics"]["top1"]["mean"] for r in t["rows"]}
    assert cells == {("fp32", 512): pytest.approx(0.761), ("fp32", 1024): pytest.approx(0.761),
                     ("int8", 512): pytest.approx(0.752), ("int8", 1024): pytest.approx(0.762)}
    assert rabbit_brain.open().experiment("e1").compare()["rows"] == t["rows"]
    pooled = [c for c in ok(capsys, "show", "c1")["finding"]["caveats"] if c["code"] == "pooled_setting"]
    assert [c["text"] for c in pooled] == ["the runs it reads hold calib 512 and 1024, pooled in one reading: a rule with --where calib=<value> reads one"]
    ok(capsys, "criterion", "add", "INT8 against FP32 at 1024", "-e", "e1", "--metric", "top1", "--subject", "int8", "--reference", "fp32",
       "--better", "--where", "calib=1024", "--id", "c2")
    assert not [c for c in ok(capsys, "show", "c2")["finding"]["caveats"] if c["code"] == "pooled_setting"]
    assert main(["status"]) == 0
    assert "· the runs it reads hold calib 512 and 1024, pooled in one reading" in capsys.readouterr().out


def test_a_unit_that_names_a_setting_with_one_value_for_every_run_is_told_at_the_write(lab, capsys):
    """Review of round 9: a session set seed 42 from a row as a shared setting and population.unit seed, with no word
    at either write, and then every run went uncounted for having no seed. Either write order is told, with the
    command that makes it per-run; a per-run unit says nothing."""
    int8_experiment(capsys)
    told = ("population.unit is seed, which has one value for every run (42): no run has a seed of its own, so none is counted. "
            "Make it per-run, with each run's value: rb spec set e1 seed --per-run.")
    said = lambda d: [w for w in d.get("warnings", []) if w.startswith("population.unit is")]
    assert said(ok(capsys, "spec", "set", "e1", "seed", "42")) == []
    assert said(ok(capsys, "spec", "set", "e1", "population.unit", "seed")) == [told]
    ok(capsys, "experiment", "add", "INT8 again", "--id", "e2", "--baseline", "fp32", "--candidate", "int8")
    assert said(ok(capsys, "spec", "set", "e2", "population.unit", "seed")) == []
    assert said(ok(capsys, "spec", "set", "e2", "seed", "42")) == [told.replace("e1", "e2")]
    assert said(ok(capsys, "spec", "set", "e2", "seed", "--per-run")) == []


def test_compare_marks_a_change_primary_only_where_it_is_the_number_the_criterion_reads(lab, capsys):
    """fp32's mean takes in a seed int8 never ran, so the table's change is not the paired difference c1 reads: the
    cell names c1 in the place of its unpaired change, which it never shows beside c1's number, and the line naming
    what the primary criteria read gives c1's own reading. Once int8 runs the
    same seeds the change is that difference, and the mark sits on it, the change said as c1's rule says it and c1's
    reading still given under the table."""
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--unit", "nats", "--minimize")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "int8 costs at most 0.5 nats", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.5", "--id", "c1")
    ok(capsys, "run", "add", "e1", "fp32.val_loss=1.30", "int8.val_loss=1.50", "--set", "seed=1")
    ok(capsys, "run", "add", "e1", "fp32.val_loss=1.40", "--set", "seed=2")
    t = ok(capsys, "compare", "e1")
    [cell] = [r["metrics"]["val_loss"] for r in t["rows"] if r["variant"] == "int8"]
    assert cell["delta"] == pytest.approx(0.15) and cell["primary"] is False and cell["unpaired"] == "unpaired means over 1 and 2 seeds"
    [p] = t["primary"]
    assert p["criterion"] == "c1" and p["reads"].startswith("+0.2 nats · paired difference, 1 seed")
    assert cell["read_by"] == [{"criterion": "c1", "reads": f"change.val_loss: {p['reads']}"}]
    assert main(["compare", "e1"]) == 0
    said = capsys.readouterr().out
    assert "1.5 (read by c1) (n=1)" in said and "+0.15" not in said and "[primary]" not in said
    assert f"read by a criterion, in the place of the unpaired means: c1 reads change.val_loss: {p['reads']}" in said
    assert f"the primary criteria read: val_loss int8 vs fp32 (c1 reads {p['reads']}); deltas are means" in said
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.62", "--set", "seed=2")
    t = ok(capsys, "compare", "e1")
    [cell] = [r["metrics"]["val_loss"] for r in t["rows"] if r["variant"] == "int8"]
    [p] = t["primary"]
    assert cell["delta"] == pytest.approx(0.21) and cell["primary"] is True and cell["said"] == "+0.21" and "read_by" not in cell
    assert p["reads"].startswith("+0.21 nats · paired difference, 2 seeds")
    assert main(["compare", "e1"]) == 0
    said = capsys.readouterr().out
    assert "+0.21 worse [primary] (n=2)" in said and f"the primary criteria read: val_loss int8 vs fp32 (c1 reads {p['reads']}); deltas are means" in said


def test_compare_says_a_level_a_primary_criterion_reads_where_it_is_not_the_table_s_mean(lab, capsys):
    """int8's mean is 5.72 over its runs, and c1 reads 5.64 from an analysis: the line naming what the primary criteria
    read gives c1's own reading, and c2's too, fp32's level the table says as its mean to fewer digits."""
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--unit", "nats", "--minimize")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    for seed in (1, 2, 3):
        ok(capsys, "run", "add", "e1", f"int8.val_loss=5.7{seed}", f"fp32.val_loss=5.6{seed}", "--set", f"seed={seed}")
    ok(capsys, "criterion", "add", "int8 under 6 nats", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "6", "--id", "c1")
    ok(capsys, "criterion", "add", "fp32 under 6 nats", "-e", "e1", "--metric", "fp32.val_loss", "--at-most", "6", "--id", "c2")
    ok(capsys, "analysis", "add", "e1", "--compare", "int8:val_loss=5.64[5.60,5.68]", "--method", "bootstrap", "--unit", "image", "--units", "200")
    t = ok(capsys, "compare", "e1")
    reads = {p["criterion"]: p.get("reads") for p in t["primary"]}
    assert reads["c1"].startswith("5.64 [5.6, 5.68] nats · bootstrap") and reads["c2"].startswith("5.62 [5.595, 5.645] nats · mean, t interval")
    assert main(["compare", "e1"]) == 0
    assert f"the primary criteria read: int8.val_loss (c1 reads {reads['c1']}), fp32.val_loss (c2 reads {reads['c2']}); deltas" in capsys.readouterr().out


def test_compare_says_the_number_a_primary_criterion_reads_with_its_rule_s_digits(lab, capsys):
    """The table says its means to 4 significant digits and its changes to 3, which can put the number a primary
    criterion reads on its threshold or across it: a change of +0.05004, which misses at most 0.05, reads +0.05. A
    marked change is said as its criterion's rule says it, on the page too, and a level the table shows as its mean
    (3847) is given under the table as its criterion reads it (within 3846.6 ± 0.05), as rb show gives it."""
    int8_experiment(capsys)
    ok(capsys, "metric", "add", "val_loss", "--minimize")
    ok(capsys, "spec", "set", "e1", "seed", "--per-run")
    ok(capsys, "criterion", "add", "int8 costs at most 0.05", "-e", "e1", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c1")
    ok(capsys, "criterion", "add", "int8 keeps 3846.6", "-e", "e1", "--metric", "int8.ppl", "--equals", "3846.6", "--tolerance", "0.05", "--id", "c2")
    for seed, (fp32, int8, ppl) in enumerate((("1.001", "1.05104", "3846.604"), ("1.002", "1.05204", "3846.605"), ("1.003", "1.05304", "3846.606")), 1):
        ok(capsys, "run", "add", "e1", f"fp32.val_loss={fp32}", f"int8.val_loss={int8}", f"int8.ppl={ppl}", "--set", f"seed={seed}")
    t = ok(capsys, "compare", "e1")
    [cell] = [r["metrics"]["val_loss"] for r in t["rows"] if r["variant"] == "int8"]
    assert cell["primary"] is True and cell["said"] == "+0.05004"
    reads = {p["criterion"]: p["reads"] for p in t["primary"]}
    assert reads["c2"] == finding(capsys, "c2")["says"]["comparison"].split(": ", 1)[1] and reads["c2"].split(" ")[0] in ("3846.60", "3846.61")
    assert main(["compare", "e1"]) == 0
    said = capsys.readouterr().out
    assert "1.052 +0.05004 worse [primary] (n=3)" in said and f"int8.ppl (c2 reads {reads['c2']})" in said
    ok(capsys, "experiment", "add", "x", "--id", "e2", "--baseline", "fp32", "--candidate", "int8")
    ok(capsys, "criterion", "add", "s", "-e", "e2", "--metric", "change.val_loss", "--at-most", "0.05", "--id", "c3")
    for _ in range(3):
        ok(capsys, "run", "add", "e2", "fp32.val_loss=1.0", "int8.val_loss=1.05000004")
    ok(capsys, "page", "--out", "page.html")
    page = re.sub(r"<[^>]+>", " ", Path("page.html").read_text(encoding="utf-8"))
    assert "0.05000004 higher than FP32" in page and "0.05 higher than FP32" not in page    # the grid's 2 digits would put it on 0.05


def _one_of_each(capsys) -> dict:
    int8_experiment(capsys)
    ids = {"experiment": "e1"}
    ids["question"] = ok(capsys, "question", "add", "Can INT8 ship?")["object"]["id"]
    ids["metric"] = ok(capsys, "metric", "add", "val_loss", "--minimize")["object"]["id"]
    ids["resource"] = ok(capsys, "resource", "add", "heldout", "--type", "dataset", "--name", "the held-out set")["object"]["id"]
    ids["criterion"] = ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5")["object"]["id"]
    ids["run"] = ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")["object"]["id"]
    ids["decision"] = ok(capsys, "decide", ids["criterion"], "accept", "-m", "ship it")["object"]["id"]
    return ids


@pytest.mark.parametrize("kind", ["question", "experiment", "metric", "criterion", "run", "resource", "decision"])
def test_show_returns_each_kind_of_object_named_by_its_kind(lab, capsys, kind):
    ids = _one_of_each(capsys)
    data = ok(capsys, "show", ids[kind])
    assert data["object"]["kind"] == kind and data["object"]["id"] == ids[kind]
    if kind == "criterion":
        f = data["finding"]
        assert f["rule_outcome"] == "meets" and data["decisions"][0]["id"] == ids["decision"]
        assert (f["review"]["decision"], f["review"]["action"], f["review"]["current"]) == (ids["decision"], "accept", True)
    if kind == "experiment":
        assert data["spec_sha256"] and data["run_ids"] == [ids["run"]] and data["findings"][0]["criterion"] == ids["criterion"]


def test_show_a_resource_counts_the_runs_and_analyses_past_its_first_twelve(lab, capsys):
    """rb show <resource> lists the first twelve runs and analyses that used it, then how many more, one said as one."""
    int8_experiment(capsys)
    ok(capsys, "resource", "add", "heldout", "--type", "dataset", "--name", "the held-out set")
    for i in range(14):
        ok(capsys, "run", "add", "e1", f"int8.val_loss=1.{i + 10}", "--input", "heldout:eval")
        ok(capsys, "analysis", "add", "e1", "--compare", f"int8:val_loss=1.{i + 10}", "--method", "a mean", "--input", "heldout:eval")
        if i in (12, 13):
            assert main(["show", "heldout"]) == 0
            said = capsys.readouterr().out.splitlines()
            more = ["  and 1 more run", "  and 1 more analysis"] if i == 12 else ["  and 2 more runs", "  and 2 more analyses"]
            assert [line for line in said if line.startswith("  and ")] == more


def test_a_call_reads_as_made_wherever_it_is_said(lab, capsys):
    """A call is said as made, in the words an experiment's state uses (accepted, investigating, parked), on the
    finding, rb show of the decision, rb decide and rb context, with its day and reason and no writer. The record keeps
    the action as given: accept, and who made it. An investigation is still going, so the finding says since when."""
    ids = _one_of_each(capsys)
    c, d = ids["criterion"], ids["decision"]
    f = finding(capsys, c)
    assert f["review"]["action"] == "accept" and re.fullmatch(r"accepted \d{4}-\d\d-\d\d: ship it", f["says"]["review"])
    assert f["review"]["by"] == HUMAN
    assert main(["show", d]) == 0
    assert capsys.readouterr().out.startswith(f"Decision {d}: accepted {c} at ")
    assert main(["decide", "e1", "park", "-m", "later"]) == 0
    assert ": parked e1: later" in capsys.readouterr().out
    ok(capsys, "decide", c, "investigate", "-m", "one more seed")
    assert re.fullmatch(r"investigating since \d{4}-\d\d-\d\d: one more seed", finding(capsys, c)["says"]["review"])
    assert main(["context"]) == 0
    said = capsys.readouterr().out
    assert f": accepted {c}: ship it" in said and ": parked e1: later" in said and HUMAN not in said


def test_show_a_setting_address_gives_the_setting_and_its_history(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    ok(capsys, "spec", "set", "e1", "lr", "3e-4")
    data = ok(capsys, "show", "e1/lr")
    s = data["object"]
    assert s["kind"] == "setting" and s["address"] == "e1/lr" and s["experiment_id"] == "e1"
    assert s["value"] == 0.0003 and s["status"] == "provisional" and s["source"] is None   # the old source stated the old value
    assert [h["op"] for h in data["history"]] == ["spec_set", "spec_verify", "spec_set"]
    assert "0.0001 -> 0.0003" in data["history"][-1]["detail"]["change"]


def test_show_a_setting_says_what_this_read_finds_of_its_source(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    assert ok(capsys, "show", "e1/lr")["caveats"] == []
    Path("configs/train.yaml").write_text(TRAIN_YAML.replace("lr: 1e-4", "lr: 3e-4"), encoding="utf-8")
    data = ok(capsys, "show", "e1/lr")
    assert data["object"]["status"] == "verified"                         # the record as rb wrote it
    [now] = data["caveats"]                                               # and what this read finds, as rb status does
    assert (now["code"], now["subject"], now["problem"]) == ("conflict", "e1/lr", True)
    assert now["text"] == "lr: the record says 0.0001, but configs/train.yaml#optim.lr now says 0.0003"     # the record's value first
    assert any((i["code"], i["subject"]) == ("conflict", "e1/lr") for i in ok(capsys, "status")["open"])
    assert main(["show", "e1/lr"]) == 0
    said = capsys.readouterr().out.splitlines()
    assert said[0].endswith("· verified · CONFLICT") and said[2] == "  ✗ lr: the record says 0.0001, but configs/train.yaml#optim.lr now says 0.0003"


@pytest.mark.parametrize("argv", [["show", "zzz"], ["show", "e1/nope"], ["spec", "set", "enone", "lr", "1"],
                                  ["criterion", "add", "x", "-e", "enone", "--metric", "m", "--at-most", "1"],
                                  ["run", "add", "enone", "m=1"], ["decide", "cnone", "accept", "-m", "x"]])
def test_an_id_that_does_not_exist_is_E_OBJECT_NOT_FOUND(lab, capsys, argv):
    int8_experiment(capsys)
    before = len(log_entries(lab))
    refused(capsys, 2, "E_OBJECT_NOT_FOUND", *argv)
    assert len(log_entries(lab)) == before


@pytest.mark.parametrize("argv", [["variant", "add", "e1", "x", "--role", "boss"], ["criterion", "add", "x", "--metric", "m", "--at-most", "abc"],
                                  ["decide", "e1", "accept"], ["frobnicate"], ["spec"]])
def test_a_bad_argument_is_an_E_USAGE_envelope(lab, capsys, argv):
    int8_experiment(capsys)
    err = refused(capsys, 2, "E_USAGE", *argv)
    assert err["message"] and "--help" in err["fix"]


def test_log_records_every_write_with_its_actor_and_the_hash_written(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    run = ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")["object"]["id"]
    entries = ok(capsys, "log", "-n", "2")["entries"]
    assert [(e["op"], e["kind"], e["id"]) for e in entries] == [("add", "criterion", "c1"), ("record", "run", run)]
    for e in entries:
        assert e["actor"] == HUMAN and e["actor_via"] and e["via"] == "cli"
    exp_dir = lab / ".rb" / "experiments" / "e1"
    assert entries[0]["path"] == ".rb/experiments/e1/criteria/c1.yaml"
    assert entries[0]["sha256"] == record_sha(exp_dir / "criteria" / "c1.yaml")
    assert entries[-1]["path"] == f".rb/experiments/e1/runs/{run}.yaml"
    assert entries[-1]["sha256"] == record_sha(exp_dir / "runs" / f"{run}.yaml")


def test_log_filters_by_object_and_by_setting(lab, capsys):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "lr", "0.1")
    ok(capsys, "spec", "set", "e1", "seed", "--unknown")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    assert [(e["op"], e["id"]) for e in ok(capsys, "log", "c1")["entries"]] == [("add", "c1")]
    about_lr = ok(capsys, "log", "e1/lr")["entries"]
    assert [(e["op"], e["detail"]["setting"]) for e in about_lr] == [("spec_set", "lr")]
    assert [e["op"] for e in ok(capsys, "log", "e1")["entries"]] == ["add", "spec_set", "spec_set"]


def test_context_is_the_markdown_briefing_with_its_sections(project, capsys):
    int8_experiment(capsys)
    ok(capsys, "question", "add", "Can INT8 ship?")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "configs/train.yaml#optim.lr")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.0", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.0", "--id", "c2")
    ok(capsys, "run", "add", "e1", "int8.val_loss=1.2")
    data = ok(capsys, "context")
    md = data["markdown"]
    assert md.startswith("# The record: INT8 quality")
    sections = {s.split("\n", 1)[0]: s for s in md.split("\n## ")[1:]}
    exp = "Experiment e1: INT8 vs FP32 (open)"
    for heading in ("How to record", "Words", "Rules", "Needs judgment", "Can be done now", exp, "Recent activity"):
        assert heading in sections, heading
    criteria = sections["Rules"]           # each criterion, with its headline on the line under it
    assert re.search(r"c1 +INT8 validation loss under 1\.5\n +estimate meets criterion · ", criteria), criteria
    assert re.search(r"c2 +INT8 validation loss under 1\.0\n +criterion not met · ", criteria), criteria
    assert "rb decide c2" in sections["Needs judgment"] and "c1" not in sections["Needs judgment"]
    assert "Can INT8 ship?" in sections["Can be done now"]
    # the heading names no one: what can be done now, whoever does it (JSON keys unchanged)
    main(["status"])
    status = capsys.readouterr().out
    assert "Agent can do" not in md and "Agent can do" not in status and "\nCan be done now (1)\n" in status
    assert "| setting | value | status | checked in |" in sections[exp]
    assert "| lr | 0.0001 | verified | configs/train.yaml#optim.lr |" in sections[exp]
    assert data["state"]["findings"] and data["state"]["open"]


def test_doctor_in_a_project_with_rb_checks_the_research_state(lab, capsys):
    int8_experiment(capsys)
    data = ok(capsys, "doctor")
    assert data["checks"][0]["check"] == "actor" and HUMAN in data["checks"][0]["detail"]
    assert data["outcome"] == {"passed": True, "failures": []}


def test_schema_prints_each_research_objects_json_schema(lab, capsys):
    for name in ("project", "question", "experiment", "metric", "criterion", "run", "analysis", "resource", "decision", "finding"):
        data = ok(capsys, "schema", name)
        wanted = {"result", "rule_outcome", "timing", "units", "integrity", "review", "history"} if name == "finding" else {"id"}
        assert data["title"] and wanted <= set(data["properties"]), name
    whole = ok(capsys, "schema", "all")          # the whole format, one JSON Schema: a file's kind picks its shape
    assert whole["$id"].endswith("rb-v1.schema.json") and whole["oneOf"]


# ---------------------------------------------------------------- every suggested command exists


RB_COMMAND = re.compile(r"(?<![\w./-])rb ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def _subparsers(parser) -> dict:
    return {n: p for a in (parser._subparsers._group_actions if parser._subparsers else []) for n, p in a.choices.items()}


def _check_commands(texts: list[str]) -> int:
    """Each `rb <command> [<sub>]` named in a text exists, and each --flag after it is one that command takes."""
    top = _subparsers(cli_parser.build_parser())
    seen = 0
    for text in texts:
        matches = list(RB_COMMAND.finditer(text))
        for i, m in enumerate(matches):
            cmd, sub = m.group(1), m.group(2)
            assert cmd in top, f"{text!r} names rb {cmd}"
            leaf = top[cmd]
            subs = _subparsers(leaf)
            if subs:
                assert sub in subs, f"{text!r} names rb {cmd} {sub}"
                leaf = subs[sub]
            segment = text[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
            options = {o for action in leaf._actions for o in action.option_strings}
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", segment):
                assert flag in options, f"{text!r}: rb {cmd} {sub or ''} has no {flag}"
            seen += 1
    return seen


def test_every_command_status_context_and_next_suggest_exists(project, capsys, monkeypatch):
    nexts = []

    def keep(*argv):
        code, env = rb(capsys, *argv)
        assert env.ok, (argv, [e.model_dump() for e in env.errors])
        nexts.extend(env.next)
        return env.data

    keep("init", "INT8 quality")
    keep("question", "add", "Can INT8 ship?")
    keep("experiment", "add", "INT8 keeps quality", "--hypothesis", "INT8 keeps quality")        # planned: nothing tests it
    keep("note", "add", "the held-out set is clean")
    keep("experiment", "add", "INT8 vs FP32", "--id", "e1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    keep("experiment", "add", "again", "--id", "e2", "--like", "e1")
    keep("variant", "add", "e1", "int4", "--role", "candidate")
    keep("spec", "set", "e1", "lr", "0.0001", "--source", "configs/train.yaml:4", "--no-verify")   # the rb spec verify hint
    keep("spec", "set", "e1", "wd", "0.01")                                                     # provisional, no source
    keep("spec", "set", "e1", "seed", "--unknown")
    keep("spec", "set", "e1", "batch_size", "16", "--source", "configs/train.yaml#batch_size")  # a conflict
    keep("spec", "set", "e1", "int8.calib", "64")                                               # a confound
    keep("spec", "set", "e1", "trial", "--per-run")
    keep("criterion", "add", "INT8 validation loss under 1.0", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.0")
    keep("criterion", "add", "twice", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "5", "--min-n", "3")
    keep("criterion", "add", "untested", "-e", "e1", "--metric", "int8.latency_ms", "--at-most", "10")
    keep("run", "add", "e1", "int8.val_loss=1.5", "fp32.top5_err=5.1", "--set", "trial=1")
    monkeypatch.setenv("CLAUDECODE", "1")
    keep("criterion", "add", "agent's", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "2")   # declared after the run: late
    monkeypatch.delenv("CLAUDECODE")
    keep("freeze", "e2", "-m", "pre-register")
    st = keep("status")
    ctx = keep("context")
    hints = [o["do"] for o in st["open"]] + nexts
    assert "rb spec verify e1 lr" in hints
    codes = {o["code"] for o in st["open"]}
    assert {"unknown", "provisional", "conflict", "confound", "undecided", "untested", "too_few_units", "open_question",
            "planned", "late", "unread_metric"} <= codes, codes
    backticked = re.findall(r"`(rb [^`]*)`", ctx["markdown"])
    assert _check_commands(hints + backticked) >= 25


@pytest.mark.parametrize("frozen", [False, True])
def test_the_confound_hint_run_as_written_clears_the_confound(lab, capsys, frozen):
    int8_experiment(capsys)
    ok(capsys, "spec", "set", "e1", "int8.calib", "64", "--source", "note:export script")
    ok(capsys, "criterion", "add", "INT8 validation loss under 1.5", "-e", "e1", "--metric", "int8.val_loss", "--at-most", "1.5", "--id", "c1")
    if frozen:
        ok(capsys, "freeze", "e1", "-m", "pre-register")
    [item] = [o for o in ok(capsys, "status")["open"] if o["code"] == "confound"]
    pattern = r'rb decide \S+ accept --why "\.\.\."' if frozen else r"rb spec vary \S+ \S+"
    hint = re.search(pattern, item["do"])
    assert hint, item["do"]
    command = hint.group(0).replace('"..."', '"calibration does not change what is compared"')
    ok(capsys, *shlex.split(command)[1:])
    problems = [c for c in ok(capsys, "show", "e1")["caveats"] if c["code"] == "confound" and c["problem"]]
    assert problems == [], (command, problems)


def run_as_written(capsys, command: str, *values: str) -> dict:
    """An rb command a hint prints, run as it is written: its placeholders given the values in turn (one value fills
    them all), the remark after it left off."""
    given = iter(values)
    filled = re.sub(r"<[^>]*>", lambda _: values[0] if len(values) == 1 else next(given), command.split("   (")[0])
    return ok(capsys, *shlex.split(filled)[1:])


def kept_source(capsys, address: str) -> dict:
    """A setting's source as it is kept, what rb read from it aside."""
    return {k: v for k, v in ok(capsys, "show", address)["object"]["source"].items() if k != "resolved"}


@needs_git
def test_a_stale_or_conflicting_setting_s_hint_records_its_source_again(lab, capsys):
    """rb status's command for a setting whose source states another value gives the source as it is kept: the file
    and its line or key, then each --quote, --term, --locator and --commit, every value quoted for a shell and one that
    starts with '-' joined to its option. Run as written with the value filled in, it records that source again and
    verifies it; the source's label in --source would name a file nobody can check. A quote no longer in its file is
    for the user to give again: the command holds a placeholder for it, and with the text that states the value now it
    verifies, its term and locator kept."""
    Path("paper.md").write_text("We train with a learning rate of 3e-4 for every run.\n", encoding="utf-8")
    Path("notes.md").write_text("The warmup we use is 500 steps.\n", encoding="utf-8")
    Path("cfg.yaml").write_text("dropout: 0.1\n", encoding="utf-8")
    Path("HOWTO.md").write_text("Run it with: python train.py --lr=3e-4 --epochs 10\n", encoding="utf-8")
    commit_all("sources")
    head = git("rev-parse", "HEAD")
    start(capsys)
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "spec", "set", "e1", "lr", "3e-4", "--source", "paper.md", "--quote", "a learning rate of 3e-4", "--term", "learning rate",
       "--locator", "§4.2, Training")
    ok(capsys, "spec", "set", "e1", "warmup_steps", "500", "--source", "notes.md:1", "--term", "warmup", "--locator", "the team's notes")
    assert rb(capsys, "spec", "set", "e1", "dropout", "0.2", "--source", "cfg.yaml#dropout", "--commit", head, "--locator", "Table 1")[0] == 1
    assert rb(capsys, "spec", "set", "e1", "flag_lr", "1e-3", "--source", "HOWTO.md", "--quote=--lr=3e-4", "--term", "lr", "--locator=-p3")[0] == 1
    kept = {name: kept_source(capsys, f"e1/{name}") for name in ("lr", "warmup_steps", "dropout", "flag_lr")}
    Path("paper.md").write_text("We train with a learning rate of 1e-3 for every run.\n", encoding="utf-8")     # the quote is gone
    Path("notes.md").write_text("The warmup we use is 800 steps.\n", encoding="utf-8")
    hints = {o["subject"]: (o["code"], o["do"]) for o in ok(capsys, "status")["open"]}
    assert hints["e1/lr"] == ("stale", "rb spec set e1 lr <the value it states now> --source paper.md --quote '<the text that states it now>' "
                                       "--term 'learning rate' --locator '§4.2, Training'   (or point --source at where the value is stated)")
    assert hints["e1/warmup_steps"] == ("conflict", "rb spec set e1 warmup_steps <the value the source states> --source notes.md:1 "
                                                    "--term warmup --locator 'the team'\"'\"'s notes'")
    assert hints["e1/dropout"] == ("conflict", f"rb spec set e1 dropout <the value the source states> --source 'cfg.yaml#dropout' "
                                               f"--locator 'Table 1' --commit {head}")
    assert hints["e1/flag_lr"] == ("conflict", "rb spec set e1 flag_lr <the value the source states> --source HOWTO.md --quote=--lr=3e-4 "
                                               "--term lr --locator=-p3")
    for name, values in (("lr", ("1e-3", "a learning rate of 1e-3")), ("warmup_steps", ("800",)), ("dropout", ("0.1",)), ("flag_lr", ("3e-4",))):
        assert run_as_written(capsys, hints[f"e1/{name}"][1], *values)["object"]["status"] == "verified", name
        assert kept_source(capsys, f"e1/{name}") == {**kept[name], **({"quote": "a learning rate of 1e-3"} if name == "lr" else {})}
    assert rb(capsys, "status", "--fail-on", "stale")[0] == 0


@needs_git
def test_a_stale_source_s_hint_gives_its_line_where_rb_last_read_it_and_no_commit_gone_from_the_repository(lab, capsys):
    """A line that moved is given where rb last read it, so the command, run as written with the value that line states
    now, verifies it there. A commit no longer in the repository is left out of the command, which then reads the file
    as it is; given back, the commit would leave the setting provisional, pinned where nothing can be read."""
    Path("notes.md").write_text("# Notes\nThe warmup we use is 500 steps.\n", encoding="utf-8")
    Path("cfg.yaml").write_text("lr: 0.1\n", encoding="utf-8")
    commit_all("sources")
    head = git("rev-parse", "HEAD")
    start(capsys)
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    ok(capsys, "spec", "set", "e1", "warmup", "500", "--source", "notes.md:2", "--term", "warmup")
    ok(capsys, "spec", "set", "e1", "lr", "--source", "cfg.yaml#lr", "--commit", head, "--locator", "v1")
    Path("notes.md").write_text("# Notes\nAn inserted line.\nThe warmup we use is 500 steps.\n", encoding="utf-8")
    assert ok(capsys, "spec", "verify", "e1", "warmup")["results"][0]["line"] == 3
    Path("notes.md").write_text("# Notes\nAn inserted line.\nThe warmup we use is 800 steps.\n", encoding="utf-8")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    git("checkout", "-q", "--orphan", "fresh")
    Path("cfg.yaml").write_text("lr: 0.2\n", encoding="utf-8")
    commit_all("history rewritten")
    git("branch", "-D", branch)
    git("reflog", "expire", "--expire=now", "--all")
    git("gc", "-q", "--prune=now")
    hints = {o["subject"]: (o["code"], o["do"]) for o in ok(capsys, "status")["open"]}
    assert hints["e1/warmup"] == ("stale", "rb spec set e1 warmup <the value it states now> --source notes.md:3 --term warmup"
                                           "   (or point --source at where the value is stated)")
    assert hints["e1/lr"] == ("stale", "rb spec set e1 lr <the value it states now> --source 'cfg.yaml#lr' --locator v1"
                                       "   (or point --source at where the value is stated)")
    for name, value in (("warmup", "800"), ("lr", "0.2")):
        assert run_as_written(capsys, hints[f"e1/{name}"][1], value)["object"]["status"] == "verified", name
    assert kept_source(capsys, "e1/warmup")["line"] == 3 and kept_source(capsys, "e1/lr")["commit"] is None
    assert rb(capsys, "status", "--fail-on", "stale")[0] == 0


@needs_git
def test_a_line_that_names_no_number_says_the_command_with_its_whole_source(lab, capsys):
    """rb spec set with no value reads one off the line; when the line names no number, the command it gives keeps the
    source as it was given, so with the value filled in it records and verifies that source."""
    Path("notes.md").write_text("Every run uses a batch size of sixteen per device\n", encoding="utf-8")
    commit_all("notes")
    head = git("rev-parse", "HEAD")
    start(capsys)
    ok(capsys, "experiment", "add", "x", "--id", "e1")
    given = ["--source", "notes.md:1", "--term", "batch size", "--locator", "§3", "--commit", head]
    error = refused(capsys, 2, "E_SOURCE_UNRESOLVED", "spec", "set", "e1", "batch", *given)
    command = error["message"].split("give the value: ", 1)[1]
    assert command == f"rb spec set e1 batch <value> --source notes.md:1 --term 'batch size' --locator '§3' --commit {head}"
    assert run_as_written(capsys, command, "sixteen")["object"]["status"] == "verified"
    assert kept_source(capsys, "e1/batch") == {"kind": "file", "path": "notes.md", "key": None, "line": 1, "quote": None, "term": "batch size",
                                               "commit": head, "pointer": None, "url": None, "locator": "§3", "note": ""}


def test_a_run_from_a_results_file_takes_only_the_keys_asked_for(project, capsys):
    """A results file also holds per-case numbers; --keys takes the summary a criterion reads, and the file is hashed whole."""
    int8_experiment(capsys)
    Path("results.json").write_text(json.dumps({"summary": {"delta_err": 0.21, "n": 64, "ok": True},
                                                "cases": {"a": {"err": 1.0}, "b": {"err": 2.0}}}), encoding="utf-8")
    data = ok(capsys, "run", "add", "e1", "--from", "results.json", "--keys", "summary.*")
    assert data["object"]["metrics"] == {"summary.delta_err": 0.21, "summary.n": 64}
    assert data["object"]["files"][0]["path"] == "results.json"
    refused(capsys, 2, "E_OBJECT_INVALID", "run", "add", "e1", "--from", "results.json", "--keys", "nothing.*")


def test_a_code_line_gives_its_value_when_none_is_typed(project, capsys):
    int8_experiment(capsys)
    Path("train.py").write_text("LR = 0.0003  # tuned on the dev split\nWD = 0.01\n", encoding="utf-8")
    s = ok(capsys, "spec", "set", "e1", "optim.lr", "--source", "train.py:1")["object"]
    assert s["value"] == 0.0003 and s["status"] == "verified"
    refused(capsys, 2, "E_SOURCE_UNRESOLVED", "spec", "set", "e1", "optim.momentum", "--source", "train.py:2")


def test_rb_output_piped_into_head_exits_quietly(project, capsys):
    """rb status | head -1: the reader takes a line and leaves, and rb ends without a word about it."""
    int8_experiment(capsys)
    proc = subprocess.Popen([*command(), "status"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment())
    first = proc.stdout.readline()
    proc.stdout.close()
    said = proc.stderr.read()
    proc.stderr.close()
    assert proc.wait(timeout=60) == 0 and said == b"" and first.strip()


def test_resource_add_file_reads_the_file_from_the_current_folder(lab, capsys, monkeypatch):
    """rb resource add --file names a file from where the command runs, as every path argument does: from a subfolder,
    the file there is hashed and recorded where it is, and reads as present."""
    import hashlib
    ok(capsys, "init", "INT8 quality")
    Path("ckpt").mkdir()
    Path("ckpt/int8.bin").write_bytes(b"int8 weights\n")
    Path("int8.bin").write_bytes(b"another file at the top, with the same name\n")
    monkeypatch.chdir("ckpt")
    res = ok(capsys, "resource", "add", "int8-ckpt", "--type", "checkpoint", "--name", "int8 checkpoint", "--file", "int8.bin")["object"]
    assert res["sha256"] == hashlib.sha256(b"int8 weights\n").hexdigest()
    assert [(loc["provider"], loc["ref"]) for loc in res["locations"]] == [("local", "ckpt/int8.bin")]
    monkeypatch.chdir("..")
    assert ok(capsys, "show", "int8-ckpt")["availability"]["state"] == "present"
