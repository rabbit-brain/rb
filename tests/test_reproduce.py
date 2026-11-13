"""Reproduce (BP 1c): an experiment's recipe and each run's, as the shell's document holds them (experiments[].reproduce,
runs{}.reproduce), from what the record holds and nothing else.

Each record here is written with rb's own commands, as the user writes it at their terminal (human:ana), and read back
through the document rb page (the Owner tier) and rb open (the Live tier) build under RB_TEST_SHELL, written out by
RB_TEST_SHELL_DUMP before it is drawn. reproduce.rs builds each recipe; shell/model.rs puts it in the document. Until the
merge point that carries both, tests/fixtures/shell/merged-phases.txt does not list P1 and these skip with "pending P1"
(RB_SHELL_CONTRACT_MERGED=P1 runs them before then); reproduce.rs's own tests (cargo test reproduce::) hold the same
rules on the ledger's writes meanwhile. The record format is checked always."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from rabbit_brain.models import Envelope
from rbtest import Open, as_the_user, command, main, now

HERE = Path(__file__).parent / "fixtures" / "shell"
SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "rb-v2.schema.json"
HAS_GIT = shutil.which("git") is not None
BRANCH = "lab-notebook"        # the branch the record is written on, which no recipe names
_spec = importlib.util.spec_from_file_location("reproduce_shape", HERE / "shape.py")
shape_py = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shape_py)


def merged() -> set:
    text = (HERE / "merged-phases.txt").read_text(encoding="utf-8")
    tags = {t.strip() for t in text.splitlines() if t.strip() and not t.startswith("#")}
    return tags | {t.strip() for t in os.environ.get("RB_SHELL_CONTRACT_MERGED", "").split(",") if t.strip()}


def needs(group: str) -> None:
    if group not in merged():
        pytest.skip(f"pending {group}")


def _git(*argv: str) -> str:
    return subprocess.run(["git", *argv], check=True, capture_output=True, text=True).stdout.strip()


class Int8:
    """The INT8 ResNet-50 on ImageNet record, and the documents built of it: e1, INT8 against FP32 with a per-run seed,
    whose seed-1 run made the int8 checkpoint as it was recorded at a clean committed tree and whose seed-2 run was made
    days before it was recorded and names no commit; e2, calibration set size, whose 512-image run recorded the bytes
    seed 1 wrote and whose 1024-image run its own; e3, the checkpoint's latency on one GPU; e4, a run rb's journal
    watched. The FP32 weights have an address and no recipe."""

    def __init__(self, ok, tmp: Path) -> None:
        self.ok, self.tmp = ok, tmp
        self.runs: dict = {}

    def run(self, name: str, *argv: str) -> str:
        self.runs[name] = self.ok("run", "add", *argv)["object"]["id"]
        return self.runs[name]

    def record_files(self) -> dict:
        """The record as git keeps it: every file under .rb/ that .rb/.gitignore leaves to git, with its bytes."""
        keep = {}
        for p in sorted(Path(".rb").rglob("*")):
            rel = p.relative_to(".rb")
            if p.is_file() and rel.parts[0] not in ("cache", "journal", ".lock") and not p.name.endswith(".tmp"):
                keep[str(rel)] = p.read_bytes()
        return keep

    def document(self, tier: str) -> dict:
        """The document rb builds for `tier`: rb page's (owner), or rb open's at its start place (live)."""
        dump = self.tmp / f"{tier}.view.json"
        if dump.exists():
            return json.loads(dump.read_text(encoding="utf-8"))
        env = {"RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            if tier == "owner":
                main(["page", "--out", str(self.tmp / "page.html")])
            else:
                server = Open()
                try:
                    try:
                        urllib.request.urlopen(server.url + "/", timeout=60).read()
                    except OSError:
                        pass                         # the renderer may not draw yet; the document is written before it
                finally:
                    server.close()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        assert dump.exists(), f"rb built no {tier} document"
        return json.loads(dump.read_text(encoding="utf-8"))

    def experiment(self, tier: str, eid: str) -> dict:
        exp = next(e for e in self.document(tier)["experiments"] if e["id"] == eid)
        assert "reproduce" in exp, f"{eid} has no reproduce"
        return exp["reproduce"]

    def of_run(self, tier: str, name: str) -> dict:
        return self.document(tier)["runs"][self.runs[name]]["reproduce"]


@pytest.fixture
def int8(workdir, monkeypatch, capsys, tmp_path_factory):
    if not HAS_GIT:
        pytest.skip("the record is played in a git repository")
    as_the_user(monkeypatch, "RB_HUB_TOKEN", "RB_HUB_URL", "RB_TEST_SHELL", "RB_TEST_SHELL_DUMP", "RB_TEST_SHELL_MODEL")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    _git("init", "-q")
    _git("checkout", "-q", "-b", BRANCH)
    _git("config", "user.email", "ana@example.org")
    _git("config", "user.name", "ana")

    def ok(*argv):
        code = main([*argv, "--json"])
        env = Envelope.model_validate_json(capsys.readouterr().out)
        assert code == 0 and env.ok, (argv, env.errors)
        return env.data

    rec = Int8(ok, tmp_path_factory.mktemp("documents"))
    ok("init", "INT8 ResNet-50 on ImageNet")
    ok("question", "add", "Is INT8 ResNet-50 as accurate as FP32 on ImageNet?", "--id", "q1")
    ok("metric", "add", "top1", "--maximize")
    ok("metric", "add", "latency_ms", "--unit", "ms", "--minimize")
    ok("experiment", "add", "INT8 against FP32: top-1 on the validation set", "--id", "e1", "-q", "q1", "--hypothesis",
       "INT8 keeps top-1 within a point of FP32", "--baseline", "fp32", "--candidate", "int8")
    ok("spec", "set", "e1", "seed", "--per-run")
    ok("spec", "set", "e1", "batch_size", "256")
    ok("spec", "set", "e1", "quant_backend", "--unknown")
    ok("criterion", "add", "INT8 loses at most a point of top-1", "-e", "e1", "-q", "q1", "--metric", "change.top1", "--at-least", "-0.01", "--id", "c1")
    ok("experiment", "add", "Calibration set size", "--id", "e2", "-q", "q1", "--purpose", "how many calibration images INT8 needs", "--candidate", "int8")
    ok("spec", "set", "e2", "calib_images", "--per-run")
    ok("experiment", "add", "INT8 latency on one GPU", "--id", "e3", "-q", "q1", "--purpose", "how fast the INT8 model answers", "--candidate", "int8")
    ok("experiment", "add", "INT8 under the journal", "--id", "e4", "-q", "q1", "--purpose", "a run rb watched", "--candidate", "int8")
    ok("resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet validation set", "--recipe", "python get_data.py --split val (scripts/README.md:5)")
    ok("resource", "add", "imagenet-calib", "--type", "dataset", "--name", "512 calibration images", "--recipe", "python get_data.py --calib 512 (scripts/README.md:6)")
    ok("resource", "add", "resnet50-fp32", "--type", "checkpoint", "--name", "ResNet-50 FP32 weights", "--url", "https://example.org/resnet50_fp32.pt")
    ok("resource", "add", "ckpt-int8", "--type", "checkpoint", "--name", "INT8 checkpoint, seed 1", "--recipe", "python quantize.py --seed 1 --calib 512 (scripts/README.md:8)")
    Path("results").mkdir()
    Path("results/seed1.json").write_text(json.dumps({"fp32": {"top1": 0.7613}, "int8": {"top1": 0.7581}}), encoding="utf-8")
    Path("results/seed2.json").write_text(json.dumps({"fp32": {"top1": 0.7609}, "int8": {"top1": 0.7574}}), encoding="utf-8")
    Path("results/calib1024.json").write_text(json.dumps({"int8": {"top1": 0.7585}}), encoding="utf-8")
    Path("bench.py").write_text("import json, os\nos.makedirs('out', exist_ok=True)\njson.dump({'int8': {'latency_ms': 3.9}}, open('out/bench.json', 'w'))\n"
                                "print(json.dumps({'metrics': {'int8.latency_ms': 3.9}}))\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "the record, its results and the benchmark")
    rec.run("seed1", "e1", "--from", "results/seed1.json", "--set", "seed=1", "--input", "imagenet-val:eval", "--input", "imagenet-calib:train",
            "--input", "resnet50-fp32:weights", "--output", "ckpt-int8:checkpoint", "--command", "python eval.py --seed 1")
    rec.run("seed2", "e1", "--from", "results/seed2.json", "--set", "seed=2", "--input", "imagenet-val:eval", "--command", "python eval.py --seed 2",
            "--produced-at", "2026-10-01T09:00:00Z")
    rec.run("c512", "e2", "--from", "results/seed1.json", "--keys", "int8.*", "--set", "calib_images=512", "--input", "imagenet-val:eval",
            "--input", "imagenet-calib:train")
    rec.run("c1024", "e2", "--from", "results/calib1024.json", "--set", "calib_images=1024", "--input", "imagenet-val:eval", "--input", "imagenet-calib:train")
    rec.run("t4", "e3", "int8.latency_ms=3.9", "--input", "ckpt-int8:model", "--host", "gpu=T4")
    assert main(["journal", "run", "--", sys.executable, "bench.py"]) == 0     # its own words after "--" are the command's
    capsys.readouterr()
    watched = [e for e in ok("journal", "-n", "50")["entries"] if e.get("op") == "run"][-1]["command"]
    rec.run("watched", "e4", "--from", "out/bench.json", "--command", watched)
    rec.watched = watched
    return rec


def flat(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def labels(recipe: dict) -> list:
    return [s["label"] for s in recipe["steps"]]


def test_reproduce_is_recorded_facts(int8):
    """Made-by order, unknown lines, no git checkout and no rb run add: e3 times the checkpoint seed 1 made, so what
    seed 1 read to make it comes first and then the model with its recipe; every row of what it takes to run it again is
    a comment of the copied script; seed 1's receipt holds the code that made it, so its recipe starts a worktree at that
    commit; seed 2 was made before it was recorded and names no commit, so the commit it was recorded at is in no line
    of its recipe."""
    needs("P1")
    seed1 = int8.runs["seed1"]
    e3 = int8.experiment("owner", "e3")
    steps = labels(e3)
    calib = steps.index("training data · imagenet-calib, which fp32 and int8 · seed 1 (e1) read to make ckpt-int8")
    weights = steps.index("weights · resnet50-fp32, which fp32 and int8 · seed 1 (e1) read to make ckpt-int8: no recipe recorded")
    model = steps.index("model · ckpt-int8 · made by fp32 and int8 · seed 1 (e1)")
    assert seed1 in e3["steps"][model]["refs"], e3["steps"][model]
    assert calib < model and weights < model and not any("imagenet-val" in s for s in steps), steps
    assert (e3["steps"][model]["command"], e3["steps"][model]["from"]) == ("python quantize.py --seed 1 --calib 512", "scripts/README.md:8")
    for eid in ("e1", "e2", "e3", "e4"):
        r = int8.experiment("owner", eid)
        lines = r["script"].splitlines()
        assert lines[0].startswith(f"# Reproduce {eid}: ") and lines[1] == "# Recipe, from what the record holds. rb did not run these commands."
        for row in r["ready"]:
            assert f"# {row['label']}: {row['text']}" in lines, (row, r["script"])
        assert [s["n"] for s in r["steps"]] == list(range(1, len(r["steps"]) + 1))
    e1 = int8.experiment("owner", "e1")
    assert e1["settings"]["unknown"] == ["quant_backend"] and "# unknown: quant_backend" in e1["script"].splitlines()
    assert e1["ready_line"].startswith("to run again: ") and e1["ready_line"].count("·") == len(set(r["state"] for r in e1["ready"])) - 1
    head = _git("rev-parse", "HEAD")
    s1, s2 = int8.of_run("owner", "seed1"), int8.of_run("owner", "seed2")
    assert s1["steps"][0]["command"] == f"git worktree add --detach ../e1-{head[:7]} {head}" and s1["steps"][0]["runnable"]
    assert f"\ngit worktree add --detach ../e1-{head[:7]} {head}\n" in s1["script"]
    code = e1["steps"][0]
    assert code["what"] == "code" and not code["runnable"] and code["command"] == ""
    assert "one of the two runs was made before it was recorded and names no commit" in code["label"]
    # a code step with nothing to run is said once in the copied script, by its ready row
    assert not any(line.startswith("# code · ") for line in e1["script"].splitlines()), e1["script"]
    assert head[:7] not in flat(s2) and "git worktree" not in flat(s2)
    for doc in ("owner", "live"):
        whole = flat([e.get("reproduce") for e in int8.document(doc)["experiments"]] + [r.get("reproduce") for r in int8.document(doc)["runs"].values()])
        assert "git checkout" not in whole and "rb run add" not in whole
    assert e1["prompt"] == "run one more top1 measurement on INT8 against FP32 and record it in rb"
    assert s1["prompt"] == ""


def test_made_recipe_names_its_one_config(int8):
    """What a run made ends its recipe, named for that run and its config: e1's seed-1 run made the checkpoint, and
    seed 2 gets nothing; e2's 512-image run recorded the bytes seed 1 wrote, so its recipe ends with the checkpoint's,
    and the 1024-image run's never holds it."""
    needs("P1")
    seed1, c512, c1024 = int8.runs["seed1"], int8.runs["c512"], int8.runs["c1024"]
    e1 = int8.experiment("owner", "e1")
    [made] = e1["made"]
    assert made == {"run": seed1, "maker": seed1, "resource": "ckpt-int8", "command": "python quantize.py --seed 1 --calib 512",
                    "from": "scripts/README.md:8", "line": "fp32 and int8 · seed 1: made ckpt-int8."}
    assert e1["script"].endswith("# fp32 and int8 · seed 1: made ckpt-int8\n# how to get it again · scripts/README.md:8\n"
                                 "python quantize.py --seed 1 --calib 512\n")
    e2 = int8.experiment("owner", "e2")
    [made] = e2["made"]
    assert made["run"] == c512 and made["maker"] == seed1
    assert made["line"] == "int8 · calib images 512: the same bytes are recorded by fp32 and int8 · seed 1 (e1), which made ckpt-int8."
    assert c1024 not in flat(e2["made"]) and "# int8 · calib images 1024:" not in e2["script"]
    one = int8.of_run("owner", "c512")
    assert [m["line"] for m in one["made"]] == ["The same bytes are recorded by fp32 and int8 · seed 1 (e1), which made ckpt-int8."]
    assert one["script"].endswith("# how to get it again · scripts/README.md:8\npython quantize.py --seed 1 --calib 512\n")
    other = int8.of_run("owner", "c1024")
    assert other["made"] == [] and "quantize" not in other["script"]
    assert int8.of_run("owner", "seed2")["made"] == []


def test_an_input_without_a_recipe_is_a_sentence(int8):
    """The FP32 weights have an address and no recipe: their step says so in words, with nothing to run and no line of
    the copied script to run, the same on both tiers; the address is the resource's own (resources{}.url, which only the
    user's own screens show). An input with a recipe is its command, with the file and line it was taken from."""
    needs("P1")
    said = "weights · resnet50-fp32: no recipe recorded"
    for tier in ("live", "owner"):
        r = int8.of_run(tier, "seed1")
        [step] = [s for s in r["steps"] if s["refs"][:1] == ["resnet50-fp32"]]
        assert (step["label"], step["command"], step["runnable"]) == (said, "", False)
        lines = r["script"].splitlines()
        at = lines.index(f"# {said}")
        assert at + 1 == len(lines) or lines[at + 1] == "" or lines[at + 1].startswith("#"), r["script"]
        [val] = [s for s in r["steps"] if s["label"] == "evaluation data · imagenet-val"]
        assert (val["command"], val["from"], val["runnable"]) == ("python get_data.py --split val", "scripts/README.md:5", True)
        assert "\n# evaluation data · imagenet-val · scripts/README.md:5\npython get_data.py --split val\n" in r["script"]
    for tier in ("live", "owner"):
        doc = int8.document(tier)
        # a recipe holds no resource's address: a public one is said only as where a reader gets it (getit.rs)
        reproduce = flat([e["reproduce"] for e in doc["experiments"]] + [r["reproduce"] for r in doc["runs"].values()])
        assert "https://" not in reproduce.replace("get it: https://", "")


def test_only_a_command_rb_watched_reads_as_observed(int8):
    """The command as given stays apart, as history: e1 counts and lists its two, neither observed, each by its kind in
    the copied script; a run's own command is a line to run only when rb knows it made the numbers, and one given later
    is said, never offered to run; the run rb journal run watched is the one observed, and its recipe says so."""
    needs("P1")
    e1 = int8.experiment("owner", "e1")
    assert e1["command_as_given"] == {"recorded": 2, "runs": 2, "line": "every run records its command (as given) · 1 given later, not observed"}
    assert [c["observed"] for c in e1["commands"]] == [False, False]
    lines = e1["script"].splitlines()
    assert "#   fp32 and int8 · seed 1: command (as given): python eval.py --seed 1" in lines, e1["script"]
    assert "#   fp32 and int8 · seed 2: command given later, not observed: python eval.py --seed 2" in lines
    assert e1["steps"][-1]["label"] == "command (as given) · recorded for all 2 runs · 1 given later, not observed" and not e1["steps"][-1]["runnable"]
    own = int8.of_run("owner", "seed1")["steps"][-1]
    assert (own["label"], own["command"], own["runnable"]) == ("command (as given)", "python eval.py --seed 1", True)
    s2 = int8.of_run("owner", "seed2")
    assert s2["steps"][-1]["label"] == "command given later, not observed: python eval.py --seed 2" and not s2["steps"][-1]["runnable"]
    assert "python eval.py --seed 2" not in s2["script"].splitlines()
    watched = int8.experiment("owner", "e4")
    assert watched["commands"] == [{"run": int8.runs["watched"], "command": int8.watched, "observed": True}]
    how = "rb journal run watched it make these numbers, "
    # named as the run's runs.{id}.named names it, after its "run ": another experiment's run reads "int8" too
    named = int8.document("owner")["runs"][int8.runs["watched"]]["named"]
    assert named.startswith("run int8, recorded "), named
    said = named.removeprefix("run ")
    assert any(line.startswith(f"#   {said}: command (as given): {int8.watched} ({how}") for line in watched["script"].splitlines()), watched["script"]
    own = int8.of_run("owner", "watched")
    assert own["steps"][-1]["label"].startswith(f"command (as given) · {how}") and own["steps"][-1]["runnable"], own["steps"][-1]
    assert f"\n# command (as given) · {how}" in own["script"] and f"\n{int8.watched}\n" in own["script"]
    # its code is what the command started with (E2): a clean checkout of its commit, without the file the run wrote
    code = own["steps"][0]
    assert code["runnable"] and code["command"].startswith("git worktree add --detach ") and " read-tree " not in code["command"], code
    worktree = Path(code["command"].split()[4])
    try:
        for line in code["command"].splitlines():
            subprocess.run(line.split(), check=True, capture_output=True)
        assert not (worktree / "out" / "bench.json").exists()
        assert (worktree / "bench.py").read_text(encoding="utf-8") == Path("bench.py").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(worktree, ignore_errors=True)


def test_a_recipe_meets_the_contract_s_shape(int8):
    """Every path of every recipe, on both tiers, is a path of the contract's shape with its type; nothing outside it."""
    needs("P1")
    lines = (HERE / "view-1.shape.tsv").read_text(encoding="utf-8").splitlines()[1:]
    shape = {row.split("\t")[0]: set(row.split("\t")[1].split("|")) for row in lines}
    for tier in ("live", "owner"):
        doc = int8.document(tier)
        part = {"experiments": [{"reproduce": e["reproduce"]} for e in doc["experiments"]], "runs": {k: {"reproduce": r["reproduce"]} for k, r in doc["runs"].items()}}
        for path, v in shape_py.nodes(part):
            if ".reproduce" in path:
                assert path in shape, f"{path} is outside the contract's shape"
                assert shape_py.kind(v) in shape[path], (path, shape_py.kind(v), shape[path])


def test_a_recipe_names_no_writer_no_branch_and_no_platform(int8):
    """A recipe says what was run, never who recorded it, on which branch, or on which machine's platform: no writer's
    words or em dash on either tier, and on a page none of the Live tier's own values."""
    needs("P1")
    from test_shell_contract import WORDING, WRITER_WORDS
    platforms = {int8.ok("show", "--long", rid)["object"]["receipt"]["environment"].get("platform") for rid in int8.runs.values()} - {None, ""}
    assert platforms, "the record keeps the platform each run was recorded on"
    for tier in ("live", "owner"):
        doc = int8.document(tier)
        whole = flat([e["reproduce"] for e in doc["experiments"]] + [r["reproduce"] for r in doc["runs"].values()])
        assert not WRITER_WORDS.search(whole) and not WORDING.search(whole), (WRITER_WORDS.search(whole), WORDING.search(whole))
        assert BRANCH not in whole and "ana" not in whole.replace("analys", "")
        if tier == "owner":
            assert not any(p in whole for p in platforms)


def said(recipe: dict) -> str:
    """The parts of a recipe a page draws as text; ids rb minted live in its data alone (refs, run, maker, commands)."""
    parts = [recipe["script"], recipe["ready_line"], recipe["prompt"], recipe["command_as_given"]["line"]]
    parts += [f"{r['label']}: {r['text']}" for r in recipe["ready"]] + [s["label"] for s in recipe["steps"]]
    parts += [m["line"] for m in recipe["made"]]
    return "\n".join(parts)


def test_a_recipe_names_runs_by_their_labels_never_their_ids(int8):
    """No id rb minted is text a page shows: a recipe's header, its per-run comment lines, what it takes to run it again
    and what a run made name each run by its label (its arms and per-run values), and a run of another experiment with
    that experiment's id, which someone chose. Per-run settings read by their names in words, as Runs says them."""
    needs("P1")
    doc = int8.document("owner")
    recipes = [e["reproduce"] for e in doc["experiments"]] + [r["reproduce"] for r in doc["runs"].values()]
    for rid in doc["runs"]:
        for r in recipes:
            assert rid not in said(r), (rid, said(r))
    assert int8.of_run("owner", "seed2")["script"].startswith("# Reproduce a run of e1 · fp32 and int8 · seed 2\n")
    assert "\n#   fp32 and int8 · seed 1: command (as given): python eval.py --seed 1\n" in int8.experiment("owner", "e1")["script"]
    assert "\n# per run: calib images 512\n" in int8.of_run("owner", "c512")["script"]
    e2 = int8.experiment("owner", "e2")["script"]
    assert "# per run (2): calib images\n" in e2 and "calib_images" not in e2, e2
    # the ids stay in the data, where a link needs them
    assert int8.runs["seed1"] in int8.experiment("owner", "e3")["steps"][-2]["refs"] or any(
        int8.runs["seed1"] in s["refs"] for s in int8.experiment("owner", "e3")["steps"])


def test_building_a_recipe_writes_nothing(int8):
    """Each document is computed on read: building both leaves every file of the record as it was."""
    needs("P1")
    before = int8.record_files()
    int8.document("owner")
    int8.document("live")
    assert int8.record_files() == before


def test_a_checkout_is_only_of_the_code_a_receipt_holds(int8):
    """A run recorded as it was made, at a clean tree at a later commit, whose receipt names an earlier commit as the code
    that made its numbers (--commit): the checkout it was recorded in did not make them, so its recipe checks out no
    commit and never names that one; what a rerun needs is the commit it names. Recorded at the commit it names, the
    same run's code is held, and its recipe starts a worktree there."""
    needs("P1")
    earlier = _git("rev-parse", "HEAD")
    Path("eval.py").write_text("print('top-1 on the validation set')\n", encoding="utf-8")
    for i, name in enumerate(("seed9", "seed10")):
        Path(f"results/{name}.json").write_text(json.dumps({"fp32": {"top1": 0.7610 + i / 1e4}, "int8": {"top1": 0.7570 + i / 1e4}}), encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "the evaluation script")
    later = _git("rev-parse", "HEAD")
    int8.run("seed9", "e1", "--from", "results/seed9.json", "--set", "seed=9", "--input", "imagenet-val:eval", "--commit", earlier)
    int8.run("seed10", "e1", "--from", "results/seed10.json", "--set", "seed=10", "--input", "imagenet-val:eval", "--commit", later)
    for tier in ("owner", "live"):
        s9 = int8.of_run(tier, "seed9")
        assert "git worktree" not in flat(s9) and later[:7] not in flat(s9), flat(s9)
        assert not s9["steps"][0]["runnable"] and s9["steps"][0]["what"] == "code"
        assert [r["text"] for r in s9["ready"] if r["label"] == "code"] == [f"ok · commit {earlier[:12]}"]
        s10 = int8.of_run(tier, "seed10")
        assert s10["steps"][0]["command"] == f"git worktree add --detach ../e1-{later[:7]} {later}" and s10["steps"][0]["runnable"]


class Yours:
    """A repository with code (train.py, eval.py, quant/calibrate.py), the INT8 record and e1's run recorded from a
    committed results file at a clean tree: rb show and rb open compare the checkout with the code that run ran."""

    def __init__(self, run, tmp: Path) -> None:
        self.run, self.tmp, self._n = run, tmp, 0

    def ok(self, *argv: str, env: dict = None) -> dict:
        r = self.run(*argv, "--json", env=env)
        assert r.returncode in (0, 1), r.stderr
        return json.loads(r.stdout)["data"]

    def checkout(self, of: str = "e1", env: dict = None) -> list:
        """rb show's checkout entries on the code row of what it takes to run `of` again."""
        rows = [r for r in self.ok("show", "--long", of, env=env)["rerunnability"] if r["what"] == "code"]
        return rows[0].get("checkout", []) if rows else []

    def said(self, of: str = "e1", env: dict = None) -> list:
        return [c["said"] for c in self.checkout(of, env)]

    def document(self, tier: str, env: dict = None) -> dict:
        self._n += 1
        dump = self.tmp / f"{tier}-{self._n}.json"
        e = {**(env or {}), "RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
        if tier == "owner":
            self.run("page", "--out", str(self.tmp / f"page-{self._n}.html"), env=e)
        else:
            old = {k: os.environ.get(k) for k in e}
            os.environ.update(e)
            try:
                server = Open()
                try:
                    urllib.request.urlopen(server.url + "/", timeout=60).read()
                finally:
                    server.close()
            finally:
                for k, v in old.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return json.loads(dump.read_text(encoding="utf-8"))


@pytest.fixture
def yours(workdir, monkeypatch, tmp_path_factory):
    if not HAS_GIT:
        pytest.skip("the record is played in a git repository")
    as_the_user(monkeypatch, "RB_HUB_TOKEN", "RB_HUB_URL", "RB_TEST_SHELL", "RB_TEST_SHELL_DUMP", "RB_TEST_SHELL_MODEL")
    cfg = tmp_path_factory.mktemp("gitconfig") / "config"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("RB_ACTOR", "human:ana")
    monkeypatch.setenv("RB_TEST_SHELL", "1")        # rb open's / draws the shell
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "ana@example.org")
    _git("config", "user.name", "ana")

    def run(*argv, env=None):
        return subprocess.run([*command(), *argv], env={**os.environ, **(env or {})}, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    Path("quant").mkdir()
    for f in ("train.py", "eval.py", "quant/calibrate.py"):
        Path(f).write_text(f"# {f}\n", encoding="utf-8")
    Path("results").mkdir()
    Path("results/s1.json").write_text(json.dumps({"fp32": {"top1": 76.1}, "int8": {"top1": 75.7}}), encoding="utf-8")
    y = Yours(run, tmp_path_factory.mktemp("documents"))
    y.ok("init", "INT8 ResNet-50 on ImageNet")
    y.ok("metric", "add", "top1", "--maximize")
    y.ok("experiment", "add", "INT8 against FP32", "--id", "e1", "--purpose", "top-1 on the validation set", "--baseline", "fp32", "--candidate", "int8")
    y.ok("criterion", "add", "INT8 loses at most a point of top-1", "-e", "e1", "--metric", "change.top1", "--at-least", "-1", "--id", "c1")
    _git("add", "-A")
    _git("commit", "-qm", "the code and the record")
    y.commit = _git("rev-parse", "HEAD")
    y.run_id = y.ok("run", "add", "e1", "--from", "results/s1.json")["object"]["id"]
    _git("add", "-A")
    _git("commit", "-qm", "the run")
    return y


def test_your_checkout_has_the_code_or_says_where_it_differs(yours):
    """checkout_differs, every case: the same commit, clean, has this code; a tracked edit differs, naming the file;
    put back while HEAD is a later commit of the record alone and a file added since, has it again; an untracked file
    never counts; another commit with other code differs. rb show says it under the code step's label, with the diff and
    the rebuild where it differs, and never offers git checkout or git restore."""
    c = yours.commit
    [entry] = yours.checkout()
    assert entry["state"] == "same" and entry["said"] == "your checkout has this code" and entry["code"] == c and entry["runs"] == [yours.run_id]
    assert entry["files"] == [] and entry["more"] == 0 and entry["diff"] == ""
    text = yours.run("show", "--long", "e1").stdout
    assert f"      code · commit {c[:12]}, which fp32 and int8 ran at\n      your checkout has this code\n" in text, text
    # a tracked edit
    Path("train.py").write_text("# train.py, edited\n", encoding="utf-8")
    [entry] = yours.checkout()
    diff = f'git diff {c} -- ":(top)" ":(top,exclude).rb"'
    assert (entry["state"], entry["said"], entry["files"], entry["diff"]) == ("differs", "your checkout differs in 1 file: train.py", ["train.py"], diff)
    text = yours.run("show", "--long", "e1").stdout
    assert f"      your checkout differs in 1 file: train.py\n      show code diff:\n        {diff}\n      rebuild:\n        git worktree add --detach ../e1-{c[:7]} {c}\n" in text, text
    assert "git checkout" not in text and "git restore" not in text
    Path("eval.py").write_text("# eval.py, edited\n", encoding="utf-8")
    Path("quant/calibrate.py").unlink()
    assert yours.said() == ["your checkout differs in 3 files: eval.py, quant/calibrate.py and train.py"]
    # put back, while HEAD is later: a note committed (the record alone) and a file added since
    _git("checkout", "--", "train.py", "eval.py", "quant/calibrate.py")
    yours.ok("note", "add", "the validation set is ImageNet-1k", "-e", "e1")
    Path("NOTES.md").write_text("notes\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "a note and NOTES.md")
    assert _git("rev-parse", "HEAD") != c
    assert yours.said() == ["your checkout has this code"]
    # untracked files never count, nor does a new results file
    Path("scratch.py").write_text("x = 1\n", encoding="utf-8")
    Path("results/s2.json").write_text("{}", encoding="utf-8")
    assert yours.said() == ["your checkout has this code"]
    # another commit with other code
    Path("eval.py").write_text("# eval.py, v2\n", encoding="utf-8")
    _git("commit", "-qam", "eval v2")
    assert yours.said() == ["your checkout differs in 1 file: eval.py"]
    # the run's own show says it under the checkout it was recorded in, which is the code it ran
    lines = yours.run("show", "--long", yours.run_id).stdout.splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith("  repository when recorded"))
    assert lines[i + 1] == "    your checkout differs in 1 file: eval.py", lines
    [entry] = yours.checkout(yours.run_id)
    assert entry["anchor"] == "recorded" and entry["said"] == "your checkout differs in 1 file: eval.py"


def test_past_three_files_your_checkout_names_three_and_counts_the_rest(yours):
    """Fourteen files differ: three are named, then "and 11 more"; the data names ten and counts the rest."""
    names = [f"m{i:02d}.py" for i in range(14)]
    for n in names:
        Path(n).write_text("v1\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "fourteen modules")
    new = _git("rev-parse", "HEAD")
    run = yours.ok("run", "add", "e1", "int8.top1=75.5", "fp32.top1=76.0", "--produced-at", now())["object"]["id"]
    for n in names:
        Path(n).write_text("v2\n", encoding="utf-8")
    [entry] = yours.checkout(run)
    assert entry["code"] == new
    assert entry["said"] == "your checkout differs in 14 files: m00.py, m01.py, m02.py and 11 more"
    assert entry["files"] == names[:10] and entry["more"] == 4


def test_your_checkout_is_said_under_the_code_a_watched_run_ran(yours):
    """A run rb journal run watched, during which a commit changed train.py: its receipt is at the later commit, and its
    held code is the commit it started at. rb show <run> says your checkout under "code that made it", never under the
    recording commit; rb open's run card draws the run's own code step and the line under it."""
    Path("mid.py").write_text(
        "import json, pathlib, subprocess\n"
        "pathlib.Path('train.py').write_text('# train.py, changed while it ran\\n')\n"
        "subprocess.run(['git', 'commit', '-qam', 'mid-run'], check=True)\n"
        "print(json.dumps({'metrics': {'int8.top1': 75.2, 'fp32.top1': 76.0}}))\n", encoding="utf-8")
    _git("add", "mid.py")
    _git("commit", "-qm", "mid.py")
    start = _git("rev-parse", "HEAD")
    r = yours.run("journal", "run", "--", sys.executable, "mid.py")
    assert r.returncode == 0, r.stderr
    later = _git("rev-parse", "HEAD")
    assert later != start
    yours.ok("reconcile", "--apply")
    runs = yours.ok("status", "--full")["runs"]
    watched = next(x["id"] for x in runs if x["id"] != yours.run_id)
    lines = yours.run("show", "--long", watched).stdout.splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith("  code that made it:"))
    assert start[:12] in lines[i] and lines[i + 1] == "    your checkout differs in 1 file: train.py", lines
    j = next(i for i, l in enumerate(lines) if l.startswith("  repository when recorded"))
    assert later[:12] in lines[j] and not any(l.startswith("    your checkout") for l in lines[j + 1:]), lines
    [entry] = yours.checkout(watched)
    assert (entry["anchor"], entry["code"]) == ("produced", start)
    doc = yours.document("live")
    step = doc["runs"][watched]["reproduce"]["code_step"]
    assert step["label"] == f"code · commit {start[:12]}, which this run ran at" and step["checkout"]["said"] == "your checkout differs in 1 file: train.py"
    page = urllib.request.urlopen  # the card as rb open draws it: the line directly under the run's own code step
    server = Open()
    try:
        card = page(server.url + f"/o/run-{watched}.html", timeout=60).read().decode("utf-8")
    finally:
        server.close()
    k = card.index(f"<p class=\"sm codestep\">{step['label']}</p>")
    assert card[k:].split("</p>", 1)[1].startswith("<p class=\"yours\">"), card[k:k + 400]
    assert 'data-fold="run-' in card[k:] and ":code-diff\"><summary>Show code diff</summary>" in card[k:]


def test_your_checkout_on_rb_open_and_never_on_a_page(yours):
    """rb open at its start place compares each runnable code step with your checkout (steps[].checkout, the line under
    the step's label, a fold with the diff); a page has no viewer's checkout: no key, no line, no glossary entry."""
    Path("train.py").write_text("# train.py, edited\n", encoding="utf-8")
    live = yours.document("live")
    [code] = [s for s in next(e for e in live["experiments"] if e["id"] == "e1")["reproduce"]["steps"] if s["what"] == "code"]
    assert code["checkout"]["said"] == "your checkout differs in 1 file: train.py" and code["checkout"]["state"] == "differs"
    assert all("checkout" in s for s in next(e for e in live["experiments"] if e["id"] == "e1")["reproduce"]["steps"])
    owner = yours.document("owner")
    assert not any("checkout" in s for e in owner["experiments"] for s in e["reproduce"]["steps"])
    assert not any("code_step" in r["reproduce"] for r in owner["runs"].values())
    html = next(yours.tmp.glob(f"page-{yours._n}.html")).read_text(encoding="utf-8")
    assert "your checkout" not in html and "Show code diff" not in html


def test_a_failing_git_is_said_and_never_read_as_the_same_code(yours, tmp_path):
    """git fails on the comparison: rb says it could not compare your checkout, never nothing, never the same code."""
    real = shutil.which("git")
    wrap = tmp_path / "bin"
    wrap.mkdir()
    (wrap / "git").write_text(f'#!/bin/sh\ncase " $* " in *" diff "*"--name-status"*) echo "fatal: broken" >&2; exit 128;; esac\nexec "{real}" "$@"\n')
    (wrap / "git").chmod(0o755)
    if sys.platform == "win32":
        pytest.skip("a shell script stands in for git")
    env = {"PATH": f"{wrap}{os.pathsep}{os.environ['PATH']}"}
    [entry] = yours.checkout(env=env)
    assert (entry["state"], entry["said"]) == ("unknown", "rb could not compare your checkout")


def test_a_reverted_edit_reads_as_the_same_code_again_on_rb_open(yours):
    """rb open keys what it compared on the files its status lists: an edit, then the file put back, reads as this code
    again once the places are listed again (about 15 s)."""
    Path("train.py").write_text("# train.py, edited\n", encoding="utf-8")
    server = Open()
    try:
        def line():
            return urllib.request.urlopen(server.url + "/", timeout=60).read().decode("utf-8")
        assert "differs in 1 file: <code>train.py</code>" in line()
        key = json.loads(urllib.request.urlopen(server.url + "/digest.json", timeout=60).read())["key"]
        _git("checkout", "--", "train.py")
        deadline = time.monotonic() + 40
        while json.loads(urllib.request.urlopen(server.url + "/digest.json", timeout=60).read())["key"] == key:
            assert time.monotonic() < deadline, "the key never moved"
            time.sleep(0.5)
        assert "your checkout has this code" in line()
    finally:
        server.close()


def test_readiness_is_computed_on_each_read(int8):
    """Guarantee 20: where each thing a rerun needs stands is computed on each read. rb open builds its document on every
    request, so a run's file gone between two requests reads as gone on the second."""
    needs("P1")
    dump = int8.tmp / "two-reads.view.json"
    env = {"RB_TEST_SHELL": "1", "RB_TEST_SHELL_DUMP": str(dump)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)

    def files_row() -> str:
        try:
            urllib.request.urlopen(server.url + "/", timeout=60).read()
        except OSError:
            pass                                 # the renderer may not draw yet; the document is written before it
        doc = json.loads(dump.read_text(encoding="utf-8"))
        return next(r["text"] for r in doc["runs"][int8.runs["c1024"]]["reproduce"]["ready"] if r["label"] == "files")

    try:
        server = Open()
        try:
            assert files_row() == "ok · the only file the runs named is here unchanged"
            Path("results/calib1024.json").unlink()
            assert files_row() == "missing · no file the runs named is here unchanged; not here: results/calib1024.json"
        finally:
            server.close()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_the_record_format_is_unchanged():
    """Reproduce adds no field to the record: rb schema is the committed schema byte for byte, and a resource keeps its
    acquisition as given (where it comes from, an address, a recipe), the command and its citation computed on read.
    (Format-forward, older rb reading a record this one wrote, is the phase gate's run over five older binaries.)"""
    out = subprocess.run([*command(), "schema", "all"], capture_output=True, check=True).stdout
    assert out == SCHEMA.read_bytes()
    acquisition = json.loads(SCHEMA.read_text(encoding="utf-8"))["$defs"]["Acquisition"]
    assert list(acquisition["properties"]) == ["source", "url", "recipe"] and acquisition["additionalProperties"] is False
