"""The golden-record scenarios: command scripts the harness runs under both implementations. A step is
("rb", "<command line>"[, "<subdirectory>", {extra env}]), ("write", "<path>", "<text>"), ("sh", "<shell command>"), or
("rbsh", "<shell command running $RB>"[, {extra env}]) for a command line that names an id only rb knows.
Every name and number here is synthetic."""
import json

AGENT = {"CLAUDECODE": "1"}
SESSION = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "golden-session-1"}
# Agents in worktrees of their own: each draws its own ids (its own seed), as separate machines would.
WIDE = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "golden-wide", "RB_TEST_SEED": "golden-wide"}
NARROW = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "golden-narrow", "RB_TEST_SEED": "golden-narrow"}
LATER = {"RB_TEST_NOW": "2026-10-01T12:00:20.000+00:00"}
# A run recorded on another machine: a newer torch, with CUDA, and nothing the harness's own machine names besides.
OTHER_MACHINE = {"RB_TEST_ENV": json.dumps({"python": "3.12.1", "platform": "Linux-6.8.0-x86_64-with-glibc2.39", "torch": "2.5.0", "cuda": "12.1"})}

TRAIN_YAML = """optim:
  lr: 0.0003
  weight_decay: 0.01
data:
  batch_size: 32
  root: data/train
eval:
  iters: 12
seed: 1
"""

TRAIN_JSON = '{"optim": {"lr": 0.0003, "betas": [0.9, 0.999]}, "model": {"depth": 18, "width": 64}, "name": "base"}\n'
TRAIN_TOML = 'title = "run"\n[optim]\nlr = 0.0003\nwarmup = 500\n[model]\ndepth = 18\n'
PAPER = """The method trains for 90 epochs.
We use a learning rate of 0.0003 with cosine decay.
On the held-out split the model reaches a top1 accuracy of 0.83 (Table 2).
"""
METRICS_1 = '{"summary": {"top1": 0.81, "top5": 0.95, "n": 400}, "cases": {"a": {"top1": 0.8}}, "note": "seed one"}\n'
METRICS_2 = '{"summary": {"top1": 0.79, "top5": 0.94, "n": 400}, "cases": {"a": {"top1": 0.7}}, "note": "seed two"}\n'
RAN_CFG = "optim:\n  lr: 0.0003\ndata:\n  batch_size: 64\nseed: 2\n"
BOOT = '{"comparisons": [{"subject": "wide", "reference": "base", "metric": "top1", "estimate": 0.021, "lo": 0.012, "hi": 0.030, "n": 200, "unit": "image"}]}\n'
REPORT = """# Bootstrap

| comparison | estimate | interval |
|---|---|---|
| wide vs base, top1 | 0.021 | [0.012, 0.030] |
"""

FOLDER_README = """# Warmup ablation

Removing warmup costs nothing measurable on the held-out split.

## Results

The no-warmup arm reaches top1 0.74 against 0.73 with warmup.

## Limitations

Two seeds only; the third is pending.

## Next steps

Run a third seed, then decide whether warmup stays.

Checked with `python3 scripts/audit_warmup.py results/warmup`.
"""
REPRODUCE = """# Reproducing

From a fresh clone:

```
python3 scripts/train.py --out results/warmup
python3 scripts/audit_warmup.py results/warmup
```
"""
AUDIT_PY = "import sys\nprint('checked', sys.argv[1:])\nsys.exit(0)\n"
AUDIT_FAIL_PY = "import sys\nprint('first line')\nprint('it failed here')\nsys.exit(3)\n"
TRAIN_PY = "print('training')\n"

EXHAUST_REPORT = """# Width study, round two

We compared wide against base on top1 (also logged as acc).
The wide arm reached 0.74 and base 0.70, a gain of 0.04 (5.7%).
Seeds: seed 1 and 2. The lr was 0.0003 throughout; e1 is the experiment.
Criterion c1 asks for at least 0.7 on wide.
A stray 0.123 and 42% and 3.14159 nowhere in the record.
Repeated: wide wide wide.
"""

# rb mcp, one conversation on stdin: every method, every tool, then the malformed and the refused. Each line is one
# JSON-RPC message, as a client sends them.
def _mcp_conversation(client: str) -> str:
    lines: list[str] = []
    ids = iter(range(1, 1000))

    def req(method: str, params=None) -> None:
        d = {"jsonrpc": "2.0", "id": next(ids), "method": method}
        if params is not None:
            d["params"] = params
        lines.append(json.dumps(d))

    def call(name: str, args=None) -> None:
        req("tools/call", {"name": name, "arguments": {} if args is None else args})

    req("initialize", {"protocolVersion": "2025-03-26", "clientInfo": {"name": client, "version": "1"}, "capabilities": {}})
    lines.append(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))
    req("ping")
    req("tools/list")
    req("resources/list")
    for uri in ("rb://context", "rb://status", "rb://schema", "rb://format", "rb://docs", "rb://nothing"):
        req("resources/read", {"uri": uri})
    call("context")
    call("status")
    call("status", {"experiment": "e1"})
    call("show", {"id": "c1"})
    call("show", {"id": "nothing"})
    call("compare", {"experiment": "e1"})
    call("log", {"n": 3})
    call("log", {"id": "e1", "n": 2})
    call("journal", {"n": 2})
    call("journal_scan")
    call("reconcile")
    call("question_add", {"text": "Does depth help?", "id": "q2", "parent": "q1", "words": "ask about depth"})
    call("question_move", {"question": "q2", "top": True})
    call("experiment_add", {"title": "deep vs base", "id": "e2", "question": ["q2"], "hypothesis": "-10% loss", "baseline": "base",
                            "candidate": ["deep"], "varies": ["model.depth"], "if_holds": "ship it", "if_not": "drop it", "note": "first"})
    call("experiment_file", {"experiment": "e2", "question": "q1"})
    call("experiment_relate", {"experiment": "e2", "relation": "extends", "target": "e1", "note": "grew"})
    call("experiment_gate", {"experiment": "e2", "if_holds": "ship deep", "why": "clearer", "words": "say ship deep"})
    call("note_add", {"text": "-starts with a dash", "experiment": "e2"})
    call("variant_add", {"experiment": "e2", "name": "shallow", "role": "ablation"})
    call("metric_add", {"name": "loss", "unit": "nats", "direction": "minimize", "alias": ["l", "xent"], "description": "the loss"})
    call("spec_set", {"experiment": "e2", "name": "optim.lr", "value": 0.001, "source": "note:from the paper"})
    call("spec_set", {"experiment": "e2", "name": "seed", "per_run": True})
    call("spec_set", {"experiment": "e2", "name": "cuda", "unknown": True, "no_verify": False})
    call("spec_set", {"experiment": "e2", "name": "layers", "value": [1, 2], "no_verify": True})
    call("spec_set", {"experiment": "e2", "from_file": "configs/train.json", "keys": ["optim.*", "model.depth"]})
    call("spec_verify", {"experiment": "e2", "names": ["optim.lr"]})
    call("spec_vary", {"experiment": "e2", "names": ["model.width"]})
    call("criterion_add", {"statement": "deep cuts loss", "experiment": "e2", "metric": "change.loss", "at_most": -0.01, "min_n": 2,
                           "noise": 0.005, "id": "c9"})
    call("run_add", {"experiment": "e2", "metrics": {"base.loss": 1.2, "deep.loss": 1.1}, "set": {"seed": 1}, "host": {"gpu": "none"},
                     "link": {"wandb": "https://example.org/r"}, "command": "python train.py --depth 2", "note": "one"})
    call("run_add", {"experiment": "e2", "metrics": {"base.loss": 1.21, "deep.loss": 1.12}, "set": {"seed": 2}, "produced_at": "unknown"})
    call("analysis_add", {"experiment": "e2", "compare": ["deep/base:loss=-0.09[-0.12,-0.06]"], "method": "bootstrap", "unit": "image",
                          "units": 100, "id": "a9"})
    call("resource_add", {"id": "data2", "type": "dataset", "name": "data two", "at": ["s3:s3://b/two"], "url": "https://example.org/two"})
    call("resource_locate", {"id": "data2", "provider": "local", "ref": "data/two"})
    call("resource_expose", {"id": "data2", "how": "inspected", "why": "looked"})
    call("freeze", {"experiment": "e2", "why": "before more runs"})
    call("decide", {"subject": "c9", "action": "accept", "why": "clear", "words": "accept it"})
    call("project_title", {"title": "mcp, read by question", "why": "name the theme the questions share", "words": "call it mcp read by question"})
    call("criterion_file", {"criterion": "c9", "question": "q2", "when_met": "Depth cuts loss.", "why": "say what c9 bears on", "amend": True})
    call("criterion_add", {"statement": "deep cuts loss on a second reading", "experiment": "e2", "metric": "change.loss", "at_most": 0,
                           "id": "c10", "amend": True, "why": "a second reading of the same arms"})
    call("criterion_relate", {"criterion": "c10", "relation": "reverses", "target": "c9", "orientation": "same", "why": "the same arms, read twice",
                              "amend": True})
    call("decide", {"subject": "q2", "action": "continue", "why": "on the linked reading", "words": "keep going on what is linked"})
    call("retract", {"subject": "a9", "why": "wrong unit"})
    call("page", {"out": "page.html"})
    call("export", {"out": "page.zip", "zip": True})
    call("export", {"out": "bundle.zip", "zip": True, "no_repo": True, "with_record": True})
    call("reconcile", {"apply": True})
    call("nothing")
    call("status", {"bogus": 1, "also": 2})
    call("status", ["ab"])
    call("status", [["experiment", "e1"]])
    call("status", "x")
    call("status", 5)
    call("run_add", {"experiment": "e2", "metrics": [1]})
    call("criterion_add", {"statement": "a", "experiment": "e2", "metric": "loss", "better": "yes"})
    req("tools/call", {"name": ["x"]})
    req("tools/call", {"name": 7})
    req("tools/call", [1, 2])
    req("tools/call")
    req("resources/read", {"uri": ["x"]})
    req("resources/read")
    req("nothing/here")
    lines.append(json.dumps({"jsonrpc": "1.0", "id": 99, "method": "ping"}))
    lines.append(json.dumps({"jsonrpc": "2.0", "id": 98}))
    lines.append(json.dumps({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "note_add", "arguments": {"text": "never run"}}}))
    lines.append(json.dumps([{"jsonrpc": "2.0", "id": 97, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/x"}, 5]))
    lines.append(json.dumps([{"jsonrpc": "2.0", "method": "notifications/x"}]))
    lines.append("[]")
    lines.append("{not json")
    lines.append("   ")
    lines.append(json.dumps({"jsonrpc": "2.0", "id": None, "method": "ping"}))
    lines.append(json.dumps({"jsonrpc": "2.0", "id": "s", "method": "initialize", "params": {"clientInfo": {"name": "Other"}, "protocolVersion": "1999"}}))
    call("note_add", {"text": "after the second initialize"})
    return "\n".join(lines) + "\n"


MCP_CONVERSATION = _mcp_conversation("Claude Code")
# A client without a name, one that names itself oddly, and lines ended every way a client may end them.
MCP_ODD = "\r\n".join([
    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "  Ünïcode Client/2.0 (beta)  "}}}),
    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "note_add", "arguments": {"text": "from the odd one"}}}),
]) + "\r" + json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}) + "\n"
MCP_NAMELESS = "\n".join([
    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "note_add", "arguments": {"text": "before any initialize"}}}),
    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"clientInfo": "not an object"}}),
    json.dumps({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"clientInfo": {"name": 5}}}),
    json.dumps({"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}}),
    json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "note_add", "arguments": {"text": "after a nameless initialize"}}}),
]) + "\n"


JOURNAL_ODD = "".join(json.dumps(x) + "\n" for x in [
    {"at": "2026-10-01T12:00:00+00:00", "op": "file", "path": "out/a.json", "change": "new", "written": "2026-10-01T11:00:00+00:00"},
    {"at": "2026-10-01T12:00:30+00:00", "op": "file", "path": "out/b.json", "change": 5, "written": "2026-10-01T12:00:00+00:00"},
    {"op": "file", "path": "out/c.json", "change": True, "written": "2026-10-01T11:00:00"},
    {"at": "2026-10-01T12:01:00+00:00", "op": "file", "path": None, "change": 2.5},
    {"at": "2026-10-01T12:02:00+00:00", "op": "git", "commit": ["a", "b"], "branch": "main", "dirty": True},
    {"at": "2026-10-01T12:03:00+00:00", "op": "git", "commit": "", "branch": None},
    {"at": "2026-10-01T12:04:00", "op": "run", "command": "python3 x.py", "exit": 0, "secs": 1.5},
    {"at": 20261001, "op": "run"},
    {"at": "2026-10-01T12:05:00+00:00", "op": "dismiss", "key": "k1", "why": "not mine"},
    {"at": "2026-10-01T12:06:00+00:00", "op": "other"},
])


CRITERIA_ODD = """rb: 1
kind: criteria
experiment: e1
criteria:
  - id: c7
    statement: fine
    metric: wide.top1
    comparator: at_least
    target: 0.6
    created_by: human:t
    created_at: '2026-10-01T12:00:00.000+00:00'
  - id: c8
    statement: no rule
    metric: wide.top1
    comparator: le
  - just a line
  - [1, 2]
  -
"""


# rb open, started and stopped: "taken" asks for a port something already holds; "stop" waits for the server's four
# lines, asks /, and sends Ctrl-C. Whatever either prints goes through with the port written as PORT.
OPEN_PY = """import os, shlex, signal, socket, subprocess, sys, urllib.request
rb = shlex.split(os.environ["RB"])
held = socket.socket()
held.bind(("127.0.0.1", 0))
held.listen()
port = held.getsockname()[1]
if sys.argv[1] == "stop":
    held.close()
p = subprocess.Popen([*rb, "open", "--port", str(port), *sys.argv[2:]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if sys.argv[1] == "stop":
    lines = [p.stderr.readline() for _ in range(4)]
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{port}/", timeout=30) as r:
        print(r.status, len(r.read()) > 1000)
    p.send_signal(signal.SIGINT)
    out, err = p.communicate(timeout=30)
    err = b"".join(lines) + err
else:
    out, err = p.communicate(timeout=30)
sys.stdout.write(out.decode().replace(str(port), "PORT"))
sys.stderr.write(err.decode().replace(str(port), "PORT"))
sys.exit(p.returncode)
"""


def _bridge(*requests) -> str:
    """Requests to `rb bridge`, one a line. (op, args) asks about the record at @ROOT@ as the SDK does; a dict is sent
    as it is; text is sent as it is, a line that is no request."""
    lines = []
    for r in requests:
        if isinstance(r, str):
            lines.append(r)
        elif isinstance(r, dict):
            lines.append(json.dumps(r))
        else:
            op, args, *more = r
            lines.append(json.dumps({"op": op, "root": "@ROOT@", **(more[0] if more else {}), "args": args}))
    return "\n".join(lines) + "\n"


# What the SDK asks when it starts a record and declares the work: every op that opens or declares, and what each
# refuses.
BRIDGE_DECLARE = _bridge(
    {"op": "open", "args": {}},
    {"op": "init", "args": {"title": "Does width help?", "id": "width"}},
    {"op": "init", "args": {"title": "again"}},
    {"op": "open", "args": {"path": "configs"}},
    {"op": "open", "args": {"path": "configs"}, "agent": "bot"},
    ("actor", {}),
    ("actor", {}, {"agent": "bot"}),
    ("actor", {}, {"via": "cli"}),
    ("add_question", {"text": "Can we ship the wide model?", "id": "q1", "parent": None}),
    ("add_question", {"text": "Does width help on the held-out split?", "id": "q2", "parent": "q1"}),
    ("add_question", {"text": "an id of its own", "id": None, "parent": None}),
    ("add_question", {"text": "x", "parent": "nope"}),
    ("add_metric", {"name": "top1", "unit": "", "direction": "maximize", "aliases": ["accuracy"], "description": "top-1 accuracy"}),
    ("add_metric", {"name": "latency_ms", "unit": "ms", "direction": "none", "aliases": [], "description": ""}),
    ("add_metric", {"name": "odd", "direction": "sideways"}),
    ("add_resource", {"id": "evalset", "type": "dataset", "name": "held-out split", "version": "2", "subset": "400 images", "sha256": None,
                      "digest": "", "source": "the team bucket", "url": "https://data.example.org/evalset", "recipe": "python make_split.py",
                      "locations": [["s3", "s3://bucket/evalset"]], "file": None, "note": "kept apart"}),
    ("add_resource", {"id": "wide-ckpt", "type": "checkpoint", "name": "wide weights", "file": "ckpt/wide.pt"}),
    ("add_resource", {"id": "bad", "type": "nonsense", "name": "bad"}),
    ("locate_resource", {"id": "wide-ckpt", "provider": "wandb", "ref": "team/proj/wide:v3", "note": "uploaded"}),
    ("expose", {"resource_id": "evalset", "how": "inspected", "why": "looked at the hard cases", "at": "2026-09-01"}),
    ("expose", {"resource_id": "evalset", "how": "selected", "why": "", "at": "unknown"}),
    ("expose", {"resource_id": "nope", "how": "inspected"}),
    ("availability", {"resource_id": "evalset"}),
    ("availability", {"resource_id": "wide-ckpt"}),
    ("availability", {"resource_id": "nope"}),
    ("add_experiment", {"title": "wide vs base", "id": "e1", "question": "q2", "hypothesis": "wide gains at least 0.02 top1", "why": "more capacity",
                        "purpose": "", "baseline": "base", "candidates": ["wide"], "varies": ["model.width"], "like": None, "note": "the first note",
                        "questions": [], "if_holds": "ship wide", "if_not": "keep base"}),
    ("add_experiment", {"title": "a latency benchmark", "id": "e2", "purpose": "how fast it is", "like": "e1", "questions": ["q1", "q2"]}),
    ("add_experiment", {"title": "again", "id": "e1"}),
    ("add_variant", {"experiment": "e1", "name": "narrow", "role": "ablation", "note": "half the width", "amend": None}),
    ("add_variant", {"experiment": "e1", "name": "other", "role": "baseline"}),
    ("add_note", {"text": "a note on the project", "experiment": None}),
    ("add_note", {"text": "budget: 4 GPU-hours", "experiment": "e1"}),
    ("set_gate", {"experiment": "e1", "if_holds": "ship wide this week", "if_not": "keep base", "why": "revised"}, {"words": "  ship it if it holds  "}),
    ("set_gate", {"experiment": "e2", "if_holds": "use it", "if_not": None, "why": ""}, {"words": None}),
    ("relate", {"experiment": "e2", "kind": "follows_from", "target": "e1", "note": "grew out of it"}),
    ("relate", {"experiment": "e2", "kind": "mirrors", "target": "e1"}),
    ("set_setting", {"experiment": "e1", "name": "model.width", "value": "64", "variant": "base", "source": "note:the default"}),
    ("set_setting", {"experiment": "e1", "name": "model.width", "value": 128, "variant": "wide", "source": "note:doubled"}),
    ("set_setting", {"experiment": "e1", "name": "optim.lr", "value": None, "source": "configs/train.yaml#optim.lr"}),
    ("set_setting", {"experiment": "e1", "name": "data.batch_size", "value": 32, "source": "configs/train.yaml#data.batch_size"}),
    ("set_setting", {"experiment": "e1", "name": "data.batch_size2", "value": 64, "source": "configs/train.yaml#data.batch_size"}),
    ("set_setting", {"experiment": "e1", "name": "lr", "value": 0.0003, "source": "paper.txt:2", "term": "learning rate", "locator": "section 2"}),
    ("set_setting", {"experiment": "e1", "name": "epochs", "value": None, "source": "paper.txt:1"}),
    ("set_setting", {"experiment": "e1", "name": "crop", "value": [224, 224], "verify": False}),
    ("set_setting", {"experiment": "e1", "name": "cuda", "value": "11.8", "note": "typed by hand"}),
    ("set_setting", {"experiment": "e1", "name": "tag", "value": "\"11.10\""}),
    ("set_setting", {"experiment": "e1", "name": "amp", "value": True}),
    ("set_setting", {"experiment": "e1", "name": "momentum", "value": 0.9, "source": "https://example.org/paper"}),
    ("set_setting", {"experiment": "e1", "name": "sha256", "unknown": True, "required": False}),
    ("set_setting", {"experiment": "e1", "name": "seed", "per_run": True}),
    ("set_setting", {"experiment": "e1", "name": "lr2", "value": 0.001, "cited": "3e-4", "cited_source": "paper.txt"}),
    ("set_setting", {"experiment": "e1", "name": "lr3", "value": 0.001, "cited": 0.0003}),
    ("set_setting", {"experiment": "e1", "name": "quoted", "value": 90, "source": "paper.txt", "quote": "trains for 90 epochs", "term": "trains for"}),
    ("set_setting", {"experiment": "e1", "name": "missing", "value": 0.5, "source": "paper.txt", "quote": "no such words"}),
    ("set_setting", {"experiment": "e1", "name": "pinned", "value": 0.0003, "source": "configs/train.yaml#optim.lr", "commit": "HEAD"}),
    ("set_setting", {"experiment": "e1", "name": "empty", "value": ""}),
    ("set_setting", {"experiment": "e1", "name": "noted", "value": None, "source": "note:somewhere"}),
    ("set_setting", {"experiment": "e1", "name": "linked", "value": None, "source": "https://example.org/x"}),
    ("set_setting", {"experiment": "e1", "name": "section", "value": None, "source": "configs/train.yaml#optim"}),
    ("set_setting", {"experiment": "e1", "name": "outside", "value": 1, "source": "/nowhere-outside/config.yaml:1"}),
    ("set_setting", {"experiment": "nope", "name": "x", "value": 1}),
    ("load", {"kind": "experiment", "id": "e1"}),
    ("load", {"kind": "experiment", "id": "nope"}),
    ("load", {"kind": "criterion", "id": "no/pe"}),
    ("load", {"kind": "nonsense", "id": "e1"}),
    ("verify_settings", {"experiment": "e1", "names": []}),
    ("verify_settings", {"experiment": "e1", "names": ["optim.lr", "lr"]}),
    ("verify_settings", {"experiment": "e1", "names": ["nope"]}),
    ("declare_varies", {"experiment": "e1", "names": ["crop"], "amend": None}),
    ("declare_varies", {"experiment": "e1", "names": ["not a name"]}),
    ("add_criterion", {"statement": "wide gains at least 0.02 top1", "experiment": "e1", "metric": "change.top1", "comparator": "at_least", "target": 0.02,
                       "tolerance": None, "source": None, "quote": None, "locator": None, "over": "mean", "min_n": 3, "noise": 0.005, "note": "",
                       "amend": None, "id": "c1", "subject": "", "reference": "", "estimand": "", "margin": None, "role": "primary"}),
    ("add_criterion", {"statement": "wide is better", "experiment": "e1", "metric": "top1", "comparator": "better", "subject": "wide", "reference": "base",
                       "role": "secondary", "id": "c2"}),
    ("add_criterion", {"statement": "as the paper reports", "experiment": "e1", "metric": "wide.top1", "comparator": "equals", "target": 0.83, "tolerance": 0.05,
                       "source": "paper.txt", "quote": "top1 accuracy of 0.83", "locator": "Table 2", "id": "c3"}),
    ("add_criterion", {"statement": "not worse by 0.01", "experiment": "e1", "metric": "change.top1", "comparator": "noninferior", "margin": 0.01,
                       "role": "exploratory", "note": "a margin", "id": "c4"}),
    ("add_criterion", {"statement": "the same within 0.01", "experiment": "e1", "metric": "top1", "comparator": "equivalent", "margin": 1, "subject": "wide",
                       "reference": "base", "estimand": "pooled", "id": "c5"}),
    ("add_criterion", {"statement": "every seed above 0.7", "experiment": "e1", "metric": "wide.top1", "comparator": "at_least", "target": 0.7, "over": "each",
                       "role": "gate", "min_n": "2", "id": "c6"}),
    ("add_criterion", {"statement": "no tolerance", "experiment": "e1", "metric": "top1", "comparator": "equals", "target": 1}),
    ("add_criterion", {"statement": "no such rule", "experiment": "e1", "metric": "top1", "comparator": "roughly", "target": 1}),
    ("add_criterion", {"statement": "nowhere", "experiment": "nope", "metric": "top1", "comparator": "at_least", "target": 1}),
    ("criteria", {"experiment": "e1"}),
    ("criteria", {"experiment": "nope"}),
    ("load", {"kind": "criterion", "id": "c3"}),
    ("finding", {"criterion_id": "c1"}),
    ("finding", {"criterion_id": "nope"}),
    ("experiment_state", {"experiment": "e1"}),
    ("freeze", {"experiment": "e1", "why": "before the runs", "amend": False}, {"words": "lock it before the runs"}),
    ("freeze", {"experiment": "e1", "why": "again"}),
    ("set_setting", {"experiment": "e1", "name": "eval.iters", "value": 12}),
    ("set_setting", {"experiment": "e1", "name": "eval.iters", "value": 12, "amend": "the config always had it"}),
    ("declare_varies", {"experiment": "e1", "names": ["eval.iters"], "amend": "declared late"}),
    ("add_variant", {"experiment": "e1", "name": "wider", "role": "candidate"}),
    ("add_variant", {"experiment": "e1", "name": "wider", "role": "candidate", "amend": "one more arm"}),
    ("add_criterion", {"statement": "latency holds", "experiment": "e1", "metric": "latency_ms", "comparator": "at_most", "target": 30, "amend": "forgot it", "id": "c7"}),
    ("freeze", {"experiment": "e1", "why": "as it stands", "amend": True}),
    ("get", {"id": "q2"}),
    ("get", {"id": "e1"}),
    ("get", {"id": "top1"}),
    ("get", {"id": "c1"}),
    ("get", {"id": "evalset"}),
    ("get", {"id": "nope"}),
    ("get", {"id": "no/pe"}),
    ("status", {"experiment": None}),
    ("log", {"last": None, "about": None}),
    ("who", {}),
    ("who", {}, {"agent": "bot"}),
    ("project", {}),
    ("records", {"kind": "question", "live": False}),
    ("records", {"kind": "experiment"}),
    ("records", {"kind": "metric"}),
    ("records", {"kind": "criterion", "live": True}),
    ("records", {"kind": "resource"}),
    ("records", {"kind": "run"}),
    ("records", {"kind": "nonsense"}),
    ("records", {"kind": "project"}),
    ("records", {}),
)

# What a script records as it runs: the runs, what refuses one, and the same recording twice.
BRIDGE_RUNS = _bridge(
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.71, "wide.top1": 0.74, "latency_ms": 25.0}, "variant": None, "per_run": {"seed": 1},
                 "config": "out/seed1/config.yaml", "command": "python eval.py --seed 1", "commit": None, "files": ["out/seed1/metrics.json"],
                 "links": {"wandb": "https://wandb.example/run/1"}, "again": None, "note": "the first run", "inputs": [["evalset", "eval"], ["evalset", "eval-again"]],
                 "outputs": [["wide-ckpt", "checkpoint"], ["wide-ckpt", ""]], "host": {"gpu": "A100", "n": "2"}, "repeats": None, "produced_at": None,
                 "replaces": None, "why": ""}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.70, "wide.top1": 0.73, "latency_ms": 24.5}, "per_run": {"seed": 2}, "command": "python eval.py --seed 2",
                 "commit": "HEAD", "produced_at": "2026-09-30T10:00:00+00:00"}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.70, "wide.top1": 0.73, "latency_ms": 24.5}, "per_run": {"seed": 2}, "command": "python eval.py --seed 2"}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.70, "wide.top1": 0.73, "latency_ms": 24.5}, "per_run": {"seed": 2}, "command": "python eval.py --seed 2",
                 "again": "a deliberate repeat"}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.69, "wide.top1": 0.75}, "per_run": {"seed": 3}, "files": ["out/seed1/config.yaml"]}),
    ("add_run", {"experiment": "e1", "metrics": {"top1": 0.76}, "variant": "wide", "per_run": {"seed": 4}, "produced_at": "unknown"}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.7, "wide.top1": 0.72}, "per_run": {"seed": "7"}}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.7, "wide.top1": 0.72}}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.7, "wide.top1": 0.72}, "per_run": {"seed": 5}, "inputs": [["nope", "eval"]]}),
    ("add_run", {"experiment": "e1", "metrics": {}, "per_run": {"seed": 6}}),
    "{\"op\": \"add_run\", \"root\": \"@ROOT@\", \"args\": {\"experiment\": \"e1\", \"metrics\": {\"wide.top1\": NaN}, \"per_run\": {\"seed\": 6}}}",
    "{\"op\": \"add_run\", \"root\": \"@ROOT@\", \"args\": {\"experiment\": \"e2\", \"metrics\": {\"latency_ms\": Infinity}}}",
    ("add_run", {"experiment": "e2", "metrics": {"latency_ms": 20}, "host": {"gpu": "A100"}}),
    ("add_run", {"experiment": "nope", "metrics": {"x": 1.0}}),
    ("finding", {"criterion_id": "c1"}),
    ("finding", {"criterion_id": "c6"}),
    ("experiment_state", {"experiment": "e1"}),
)

# What the SDK asks once there are runs: a repeat, a replacement and an analysis that name a run (@RUN@), the readings,
# the calls with the user's words, and a retraction.
BRIDGE_READ = _bridge(
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.71, "wide.top1": 0.745}, "per_run": {"seed": 1}, "repeats": "@RUN@"}),
    ("add_run", {"experiment": "e1", "metrics": {"base.top1": 0.72, "wide.top1": 0.75}, "per_run": {"seed": 1}, "replaces": "@RUN@", "why": "the first export was stale"}),
    ("add_analysis", {"experiment": "e1", "comparisons": [{"subject": "wide", "reference": "base", "metric": "top1", "estimate": 0.031, "lo": 0.012, "hi": 0.05,
                                                             "n": 200, "unit": "image"}, {"subject": "wide", "metric": "top1", "estimate": 0.74}],
                      "title": "paired bootstrap", "reads": ["@RUN2@"], "inputs": [["evalset", "eval"]], "files": [], "command": "python boot.py",
                      "method": "paired bootstrap over images", "unit": "image", "units": 200, "draws": 10000, "level": 0.95, "source": None, "quote": None,
                      "computed": False, "supersedes": [], "why": "", "produced_at": None, "note": "the bootstrap", "id": "a1"}),
    ("add_analysis", {"experiment": "e1", "comparisons": [{"subject": "wide", "metric": "top1", "estimate": 0.83}], "source": "paper.txt",
                      "quote": "top1 accuracy of 0.83", "method": "read off the paper", "produced_at": "2026-09-29"}),
    ("add_analysis", {"experiment": "e1", "comparisons": [{"subject": "wide", "reference": "base", "metric": "top1", "estimate": 0.032, "lo": 0.013, "hi": 0.051}],
                      "supersedes": ["a1"], "why": "more draws", "computed": True, "files": ["out/seed1/metrics.json"], "id": "a2"}),
    ("add_analysis", {"experiment": "e1", "comparisons": [{"subject": "wide", "metric": "top1"}]}),
    ("add_analysis", {"experiment": "e1", "comparisons": [{"subject": "wide", "metric": "top1", "estimate": 1}], "units": "many", "level": 3}),
    ("finding", {"criterion_id": "c1"}),
    ("finding", {"criterion_id": "c2"}),
    ("finding", {"criterion_id": "c3"}),
    ("compare", {"experiment": "e1"}),
    ("compare", {"experiment": "e2"}),
    ("compare", {"experiment": "nope"}),
    ("status", {"experiment": "e1"}),
    ("status", {"experiment": "nope"}),
    ("context", {}),
    ("log", {"last": 20, "about": None}),
    ("log", {"last": 5, "about": "e1"}),
    ("log", {"last": -3, "about": "e1/optim.lr"}),
    ("log", {"last": 0}),
    ("rerunnability", {"experiment": "e1"}),
    ("rerunnability", {"experiment": "e2"}),
    ("experiment_state", {"experiment": "e1"}),
    ("decide", {"subject": "c1", "action": "accept", "why": "within budget", "revisit": ""}, {"words": "good enough, accept it"}),
    ("decide", {"subject": "e1/cuda", "action": "accept", "why": "read it off the cluster image", "revisit": ""}, {"words": ""}),
    ("decide", {"subject": "e1", "action": "park", "why": "two seeds do not settle it", "revisit": "when the third seed is in"}, {"words": "Park it."}),
    ("decide", {"subject": "q2", "action": "investigate", "why": "needs more"}, {"agent": "bot", "words": "have another look"}),
    ("decide", {"subject": "e1", "action": "ponder", "why": "x"}),
    ("decide", {"subject": "nope", "action": "accept", "why": "x"}),
    ("decide", {"subject": "c2", "action": "accept", "why": ""}),
    ("experiment_state", {"experiment": "e1"}),
    ("set_project_title", {"title": "Width on the held-out split", "why": "name the theme the questions share"}, {"words": "call it width on the held-out split"}),
    # the first question links come after the call on q2, which was made on what is filed under q2 and now reads as made
    # on an earlier reading
    ("file_criterion", {"criterion": "c2", "question": "q2", "when_met": "The wider model is better on the held-out split.", "why": "say what c2 bears on",
                        "amend": "linked after the freeze"}),
    ("add_criterion", {"statement": "wide is better on a second reading", "experiment": "e1", "metric": "top1", "comparator": "better", "subject": "wide",
                       "reference": "base", "role": "exploratory", "id": "c9", "amend": "a second reading of the same arms", "questions": ["q2"],
                       "when_met": "The wider model is better on a second reading too."}),
    ("relate_criterion", {"criterion": "c9", "relation": "reverses", "target": "c2", "orientation": "same", "why": "the same arms, read twice",
                          "amend": "related after the freeze"}),
    # a call on q2 through the SDK, now on the readings linked to it and their declared reversal
    ("decide", {"subject": "q2", "action": "continue", "why": "on the linked readings", "revisit": ""}, {"words": "keep going on what is linked"}),
    ("retract", {"subject": "@RUN2@", "why": "the export was stale"}, {"words": "that run used the old export, drop it"}),
    ("retract", {"subject": "e1/momentum", "why": "not a setting"}),
    ("retract", {"subject": "nope", "why": "x"}),
    ("retract", {"subject": "c4", "why": ""}),
    ("get", {"id": "@RUN@"}),
    ("get", {"id": "@RUN2@"}),
    ("get", {"id": "a1"}),
    ("records", {"kind": "run"}),
    ("records", {"kind": "run", "live": True}),
    ("records", {"kind": "analysis"}),
    ("records", {"kind": "decision"}),
    ("records", {"kind": "criterion", "live": True}),
    ("status", {}),
    ("log", {"last": None}),
)

# What the user's screen asks: what rb found in the repository (@FOUND@), the home view, each card, a dismissal;
# then a bundle written, and a project cloned from it.
BRIDGE_SCREEN = _bridge(
    ("found", {}),
    ("scan", {}),
    ("scan", {}),
    ("found", {}),
    ("reconcile", {}),
    ("journal", {"last": 20}),
    ("journal", {"last": 2}),
    ("journal", {"last": None}),
    ("mark_seen", {}, {"via": "cli"}),
    ("home", {"since_baseline": None}, {"via": "cli"}),
    ("home", {"since_baseline": 3}, {"via": "cli"}),
    ("mark_seen", {}, {"via": "cli"}),
    ("card", {"ref": "@FOUND@"}, {"via": "cli"}),
    ("card", {"ref": "jnothing"}, {"via": "cli"}),
    ("object_card", {"subject": "q1"}, {"via": "cli"}),
    ("object_card", {"subject": "e1"}, {"via": "cli"}),
    ("object_card", {"subject": "e2"}, {"via": "cli"}),
    ("object_card", {"subject": "c1"}, {"via": "cli"}),
    ("object_card", {"subject": "@RUN2@"}, {"via": "cli"}),
    ("object_card", {"subject": "a1"}, {"via": "cli"}),
    ("object_card", {"subject": "evalset"}, {"via": "cli"}),
    ("object_card", {"subject": "top1"}, {"via": "cli"}),
    ("object_card", {"subject": "nope"}, {"via": "cli"}),
    ("status", {}, {"via": "cli"}),
    ("actor", {}, {"via": "cli"}),
    ("dismiss", {"key": "@KEY@", "why": "a smoke test"}, {"via": "cli"}),
    ("home", {}, {"via": "cli"}),
    ("reconcile", {}),
    ("export", {"out": None, "zip": False, "url": None, "no_repo": False, "with_record": True}),
    ("export", {"out": "exported.zip", "zip": True, "url": "https://example.org/width", "no_repo": True, "with_record": True}),
    ("export", {"out": "unaddressed.zip", "zip": True, "url": "https://example.org/width"}),
    ("export", {"out": ".rb/x"}),
    {"op": "clone", "args": {"source": "rb-export", "dest": "../home/cloned"}},
    {"op": "clone", "args": {"source": "rb-export", "dest": "../home/cloned"}},
    {"op": "clone", "args": {"source": "rb-export", "dest": "cloned"}},
    {"op": "clone", "args": {"source": "configs", "dest": "../home/cloned2"}},
    {"op": "clone", "args": {"source": "exported.zip", "dest": "../home/cloned3"}, "agent": "bot"},
    {"op": "status", "root": "@ROOT@/../home/cloned", "args": {}},
    {"op": "log", "root": "@ROOT@/../home/cloned3", "args": {}},
    {"op": "add_run", "root": "@ROOT@/../home/cloned", "args": {"experiment": "e1", "metrics": {"base.top1": 0.7, "wide.top1": 0.74}, "per_run": {"seed": 1}}},
    {"op": "finding", "root": "@ROOT@/../home/cloned", "args": {"criterion_id": "c1"}},
)

# What is not a request, and what each kind of argument refuses: said as an answer, never a crash.
BRIDGE_REFUSED = _bridge(
    "not json",
    "[1, 2]",
    "\"text\"",
    "{\"root\": \"@ROOT@\"}",
    "{\"op\": 5}",
    "",
    "   ",
    {"op": "status", "root": "@ROOT@", "args": [1]},
    {"op": "status", "root": "@ROOT@", "via": "mcp"},
    {"op": "status", "root": "@ROOT@", "via": 5},
    {"op": "actor", "root": "@ROOT@", "via": ""},
    {"op": "actor", "root": "@ROOT@", "agent": 5},
    {"op": "actor", "root": "@ROOT@", "agent": ""},
    {"op": "actor", "root": "@ROOT@", "words": 5},
    {"op": "actor"},
    {"op": "actor", "root": ""},
    {"op": "actor", "root": 5},
    {"op": "actor", "root": "@ROOT@/nowhere"},
    {"op": "status", "root": "@ROOT@/nowhere"},
    {"op": "nope", "root": "@ROOT@"},
    {"op": "init", "args": {"title": None}},
    {"op": "init", "args": {"title": 5}},
    {"op": "open", "args": {"path": 5}},
    {"op": "clone", "args": {"source": ["x"]}},
    ("add_question", {"text": None}),
    ("add_question", {"text": {"a": 1}}),
    ("add_question", {"text": "x", "id": 5}),
    ("add_metric", {"name": "m", "aliases": "one"}),
    ("add_metric", {"name": "m", "aliases": ["one", 2]}),
    ("add_resource", {"id": "r", "type": "dataset", "name": "r", "locations": [["s3"]]}),
    ("add_resource", {"id": "r", "type": "dataset", "name": "r", "locations": "s3"}),
    ("add_criterion", {"statement": "s", "experiment": "e1", "metric": "top1", "comparator": "at_least", "target": "0.5"}),
    ("add_criterion", {"statement": "s", "experiment": "e1", "metric": "top1", "comparator": "at_least", "target": True}),
    ("add_run", {"experiment": "e1", "metrics": [1]}),
    ("add_run", {"experiment": "e1", "metrics": {"wide.top1": "0.7"}, "per_run": {"seed": 6}}),
    ("add_run", {"experiment": "e1", "metrics": {"wide.top1": True}, "per_run": {"seed": 6}}),
    ("add_run", {"experiment": "e2", "metrics": {"latency_ms": 123456789012345678901234567890}}),
    ("add_criterion", {"statement": "s", "experiment": "e2", "metric": "latency_ms", "comparator": "at_most", "target": 10 ** 400}),
    {"op": "status", "root": "@ROOT@", "args": None},
    ("add_run", {"experiment": "e1", "metrics": {"x": 1.0}, "links": {"wandb": 5}}),
    ("add_run", {"experiment": "e1", "metrics": {"x": 1.0}, "host": [1]}),
    ("add_run", {"experiment": "e1", "metrics": {"x": 1.0}, "inputs": [["a", "b", "c"]]}),
    ("add_analysis", {"experiment": "e1", "comparisons": {"subject": "wide"}}),
    ("add_analysis", {"experiment": "e1", "comparisons": ["wide"]}),
    ("add_analysis", {"experiment": "e1", "comparisons": [], "computed": "yes"}),
    ("set_setting", {"experiment": "e1", "name": "x", "value": 1, "verify": "no"}),
    ("set_setting", {"experiment": "e1", "name": "x", "value": {"a": 1}, "amend": "a mapping is not a value"}),
    ("log", {"last": 2.5}),
    ("log", {"last": True}),
    ("log", {"last": 123456789012345678901234567890}),
    ("log", {"last": -123456789012345678901234567890}),
    ("journal", {"last": "2"}),
    ("home", {"since_baseline": "3"}, {"via": "cli"}),
    ("freeze", {"experiment": "e2", "why": "x", "amend": 1}),
    ("status", {"experiment": 5}),
)

SCENARIOS = {
    "basics": {"steps": [
        ("rb", "init 'Does width help?' --no-agents"),
        ("rb", "question add 'Does width help?' --id q1"),
        ("rb", "question add 'Is it the batch size?' --parent q1 --id q2"),
        ("rb", "question add 'Something else'"),
        ("rb", "question move q2 --top"),
        ("rb", "question move q2 --parent q1"),
        ("rb", "experiment add 'wide vs base' --id e1 -q q1 --hypothesis 'wider is better' -m 'more capacity' --baseline base --candidate wide --varies model.width --note 'a first note' --if-holds 'ship wide' --if-not 'keep base'"),
        ("rb", "experiment add 'a sweep' --purpose 'map the learning rate' -q q2"),
        ("rb", "experiment add 'like e1' --like e1 --id e3"),
        ("rb", "experiment file e1 q2"),
        ("rb", "experiment file e1 q2 --remove"),
        ("rb", "experiment relate e3 follows_from e1 --note 'grew out of it'"),
        ("rb", "experiment gate e1 --if-holds 'ship wide now' -m 'the deadline moved'"),
        ("rb", "experiment gate e1 --if-holds 'ship wide now'"),
        ("rb", "note add 'the project note'"),
        ("rb", "note add 'about e1' -e e1"),
        ("rb", "variant add e1 narrow --role ablation --note 'half width'"),
        ("rb", "metric add top1 --maximize --alias accuracy --unit fraction --description 'top-1 accuracy'"),
        ("rb", "metric add loss --minimize"),
        ("rb", "metric add speed"),
        ("rb", "experiment add 'bad' --id 'bad/id'"),
        ("rb", "question move nope --top"),
        ("rb", "question add 'json mode' --json"),
        ("rb", "experiment add 'json mode exp' --baseline a --candidate b --json"),
        ("rb", "status"),
        ("rb", "--json status"),
        ("rb", "status e1"),
        ("rb", "status nope"),
        ("rb", "status --fail-on bogus,unmet"),
        ("rb", "status --fail-on untested --fail-on unestablished"),
        ("rb", "status", "", SESSION),
        ("rb", "note add 'after a look'", "", SESSION),
        ("rb", "page"),
    ]},
    "settings": {"steps": [
        ("write", "configs/train.yaml", TRAIN_YAML),
        ("write", "configs/train.json", TRAIN_JSON),
        ("write", "configs/train.toml", TRAIN_TOML),
        ("write", "papers/method.txt", PAPER),
        ("rb", "init settings --no-agents"),
        ("rb", "experiment add e --id e1 --baseline base --candidate wide --varies model.width"),
        ("rb", "spec set e1 optim.lr --source configs/train.json#optim.lr"),
        ("rb", "spec set e1 model.depth 18 --source configs/train.toml#model.depth"),
        ("rb", "spec set e1 optim.warmup 400 --source configs/train.toml#optim.warmup"),
        ("rb", "spec set e1 lr 0.0003 --source papers/method.txt --quote 'learning rate of 0.0003' --term 'learning rate' --locator 'section 4'"),
        ("rb", "spec set e1 epochs 90 --source papers/method.txt:1"),
        ("rb", "spec set e1 url_thing 3 --source https://example.org/x"),
        ("rb", "spec set e1 noted 4 --source 'note:said in standup'"),
        ("rb", "spec set e1 cuda --unknown"),
        ("rb", "spec set e1 opt_thing --unknown --optional"),
        ("rb", "spec set e1 seed --per-run"),
        ("rb", "spec set e1 base.model.width 64 --source configs/train.json#model.width"),
        ("rb", "spec set e1 model.width 128 --variant wide --no-verify"),
        ("rb", "spec set e1 schedule cosine --cited step --cited-source papers/method.txt"),
        ("rb", "spec set e1 bad.lr 1"),
        ("rb", "spec set e1 empty ''"),
        ("rb", "spec set e1 list '[1, 2, 3]'"),
        ("rb", "spec set e1 text '\"6\"'"),
        ("rb", "spec set e1 --from configs/train.json"),
        ("rb", "spec set e1 --from configs/train.json --keys 'optim.*,name'"),
        ("rb", "spec set e1 --from configs/train.json --keys 'nothing.*'"),
        ("rb", "spec verify e1"),
        ("rb", "spec verify e1 optim.lr lr"),
        ("rb", "spec vary e1 data.root,optim.betas"),
        ("write", "configs/train.json", TRAIN_JSON.replace("0.0003", "0.0005")),
        ("rb", "spec verify e1"),
        ("rb", "spec verify e1 optim.lr --json"),
        ("rb", "spec set e1 depth2 --source configs/train.toml#model.depth --json"),
        ("rb", "status"),
        ("rb", "status --fail-on stale,unknown --json"),
    ]},
    "criteria": {"steps": [
        ("write", "papers/paper.txt", PAPER),
        ("rb", "init criteria --no-agents"),
        ("rb", "experiment add e --id e1 --baseline base --candidate wide"),
        ("rb", "metric add top1 --maximize"),
        ("rb", "metric add val_loss --minimize --unit nats"),
        ("rb", "criterion add 'wide costs at most 0.05' -e e1 --metric change.val_loss --at-most 0.05 --min-n 3 --noise 0.01 --id c1"),
        ("rb", "criterion add 'wide reaches 0.8' -e e1 --metric wide.top1 --at-least 0.8"),
        ("rb", "criterion add 'equals' -e e1 --metric top1 --equals 0.8 --tolerance 0.01 --role secondary"),
        ("rb", "criterion add 'better' -e e1 --metric top1 --subject wide --reference base --better --role exploratory"),
        ("rb", "criterion add 'noninferior' -e e1 --metric val_loss --subject wide --reference base --noninferior 0.1"),
        ("rb", "criterion add 'equivalent' -e e1 --metric val_loss --subject wide --reference base --equivalent 0.2 --role gate"),
        ("rb", "criterion add 'each' -e e1 --metric wide.top1 --at-least 0.5 --each"),
        ("rb", "criterion add 'bound' -e e1 --metric change.val_loss --at-most 0.2 --bound"),
        ("rb", "criterion add 'cited' -e e1 --metric top1 --equals 0.83 --tolerance 0.01 --source papers/paper.txt --quote 'top1 accuracy of 0.83' --locator 'Table 2'"),
        ("rb", "criterion add 'cited wrong' -e e1 --metric top1 --equals 0.9 --tolerance 0.01 --source papers/paper.txt --quote 'top1 accuracy of 0.83'"),
        ("rb", "criterion add 'two rules' -e e1 --metric top1 --at-most 1 --at-least 0"),
        ("rb", "criterion add 'no rule' -e e1 --metric top1"),
        ("rb", "criterion add 'speedy' -e e1 --metric speed --subject wide --reference base --better"),
        ("rb", "criterion add 'neg' -e e1 --metric change.val_loss --at-most -1e-3"),
        ("rb", "criterion add 'agent one' -e e1 --metric wide.top1 --at-least 0.7 --words 'make sure wide hits 0.7'", "", AGENT),
        ("rb", "criterion add 'json' -e e1 --metric wide.top1 --at-least 0.6 --json"),
        ("rb", "status"),
        ("rb", "status --fail-on unestablished --json"),
    ]},
    "runs": {"steps": [
        ("write", "configs/train.yaml", TRAIN_YAML),
        ("write", "out/1/metrics.json", METRICS_1),
        ("write", "out/2/metrics.json", METRICS_2),
        ("write", "out/2/config.yaml", RAN_CFG),
        ("write", "out/model.bin", "weights\n"),
        ("rb", "init runs --no-agents"),
        ("rb", "experiment add e --id e1 --baseline base --candidate wide"),
        ("rb", "spec set e1 seed --per-run"),
        ("rb", "spec set e1 data.batch_size 32 --no-verify"),
        ("rb", "criterion add 'wide top1' -e e1 --metric wide.summary.top1 --at-least 0.7 --id c1"),
        ("rb", "criterion add 'gain' -e e1 --metric change.top1 --at-least 0 --id c2"),
        ("rb", "resource add heldout --type dataset --name 'held-out set' --url https://example.org/heldout --at s3:s3://bucket/heldout"),
        ("rb", "resource add ckpt --type checkpoint --name 'wide checkpoint' --file out/model.bin"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --set seed=1"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --set seed=1"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --set seed=1 --again -m 'a second execution'"),
        ("rb", "run add e1 --from out/1/metrics.json --keys 'summary.*' --variant wide --set seed=1 --input heldout:eval --output ckpt:checkpoint --host provider=local --host gpu=none --link wandb=https://example.org/run/1 --command 'python eval.py --seed 1' --artifact out/model.bin"),
        ("rb", "run add e1 --from out/1/metrics.json --keys 'summary.*' --variant wide --set seed=1"),
        ("rb", "run add e1 --from out/2/metrics.json --keys 'summary.*' --variant wide --set seed=2 --config out/2/config.yaml --produced-at 2026-09-30"),
        ("rb", "run add e1 wide.top1=0.71 base.top1=0.70 --set seed=3 --produced-at unknown"),
        ("rb", "run add e1 wide.top1=0.72 --set seed=4 --commit abc123 --note 'from a branch'"),
        ("rb", "run add e1 wide.top1=0.73"),
        ("rb", "run add e1 wide.top1=abc --set seed=5"),
        ("rb", "run add e1 justtext --set seed=5"),
        ("rb", "run add e1 wide.top1=0.75 --set seed=5 --set other=1"),
        ("rb", "run add e1 val_loss=1.0 --variant nope --set seed=6"),
        ("rb", "run add e1 --from out/1/metrics.json --keys 'nothing.*' --set seed=6"),
        ("rb", "run add e1 --keys 'summary.*' --set seed=6"),
        ("rb", "run add e1 wide.top1=0.76 --set seed=6 --produced-at yesterday"),
        ("rb", "run add e1 wide.top1=0.77 base.top1=0.71 --set seed=7 --json", "", AGENT),
        ("rb", "run add e1 wide.top1=0.78 base.top1=0.71 --set seed=8", "", SESSION),
        ("rb", "run add e1 wide.top1=0.79 base.top1=0.71 --set seed=9", "", SESSION),
        ("rb", "status"),
        ("rb", "status --fail-on unmet,late --json"),
        ("rb", "status e1 --fail-on unresolved"),
    ]},
    "analyses": {"steps": [
        ("write", "analysis/boot.json", BOOT),
        ("write", "analysis/REPORT.md", REPORT),
        ("rb", "init analyses --no-agents"),
        ("rb", "experiment add e --id e1 --baseline base --candidate wide"),
        ("rb", "metric add top1 --maximize"),
        ("rb", "criterion add 'wide better' -e e1 --metric top1 --subject wide --reference base --better --id c1"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.72 --id-not-a-flag"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.72"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.021[0.012,0.030]' --method 'paired bootstrap over images' --unit image --units 200 --draws 10000 --id a1"),
        ("rb", "analysis add e1 --from analysis/boot.json --method 'paired bootstrap' --unit image --units 200 --command 'python boot.py'"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.021[0.012,0.030]' --source analysis/REPORT.md --quote '| wide vs base, top1 | 0.021 | [0.012, 0.030] |' --locator 'Table 1'"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.5[0.4,0.6]' --source analysis/REPORT.md"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.022[0.013,0.031]' --supersedes a1 -m 'more draws'"),
        ("rb", "analysis add e1 --compare 'garbage'"),
        ("rb", "analysis add e1 --compare 'wide:top1=0.81' --compare 'new/old:score@pooled=0.12[0.07,0.17]' --level 0.9"),
        ("rb", "status"),
        ("rb", "status --json"),
    ]},
    "calls": {"steps": [
        ("rb", "init calls --no-agents"),
        ("rb", "question add 'the question' --id q1"),
        ("rb", "experiment add e --id e1 -q q1 --baseline base --candidate wide"),
        ("rb", "spec set e1 lr 0.001 --no-verify"),
        ("rb", "criterion add 'wide is at least 0.7' -e e1 --metric wide.top1 --at-least 0.7 --id c1"),
        ("rb", "freeze e1 -m 'before the runs'"),
        ("rb", "freeze e1 -m 'again'"),
        ("rb", "spec set e1 lr 0.002 --no-verify"),
        ("rb", "spec set e1 lr 0.002 --no-verify --amend -m 'the paper said 0.002'"),
        ("rb", "criterion add 'late one' -e e1 --metric wide.top1 --at-least 0.6 --amend -m 'one more'"),
        ("rb", "run add e1 wide.top1=0.71 base.top1=0.69"),
        ("rb", "run add e1 wide.top1=0.72 base.top1=0.69 --command 'second'"),
        ("rb", "decide c1 accept -m 'looks right' --revisit 'after the third seed'"),
        ("rb", "decide e1 park -m 'waiting on data' --words 'park it for now'", "", AGENT),
        ("rb", "decide q1 investigate -m 'not settled'"),
        ("rb", "decide e1/lr accept -m 'read it off the paper'"),
        ("rb", "decide e1 bogus -m x"),
        ("rb", "retract e1/lr -m 'wrong setting'"),
        ("rb", "retract c1 -m 'bad criterion'"),
        ("rb", "freeze e1 --amend -m 'adopt as it is'"),
        ("rb", "decide e1 ship -m 'done' --json"),
        ("rb", "status"),
        ("rb", "status --fail-on undecided --json"),
        ("rb", "page"),
        # the first question link: the call on q1 made while nothing was linked (on what is filed under q1, as earlier
        # builds record it) reads as made on an earlier reading; the next is made on the linked reading, and refiling c3
        # with new words moves that call too; the late rule stays unassigned
        ("rb", "project title 'Calls on the width study' --why 'name the work, not one question'"),
        ("rb", "criterion add 'wide reaches 0.75' -e e1 --metric wide.top1 --at-least 0.75 -q q1 --when-met 'The wider model is worth shipping.' --amend -m 'the bar the team ships on' --id c3"),
        ("rb", "run add e1 wide.top1=0.78 base.top1=0.70 --command 'third'"),
        ("rb", "status"),
        ("rb", "decide q1 investigate -m 'on the linked reading'"),
        ("rb", "criterion file c3 q1 --when-met 'The wider model is worth shipping at this bar.' --amend --why 'say which bar'"),
        ("rb", "show q1"),
        ("rb", "status --fail-on undecided --json"),
    ]},
    "retractions": {"steps": [
        ("rb", "init retractions --no-agents"),
        ("rb", "experiment add e --id e1 --baseline base --candidate wide"),
        ("rb", "criterion add 'wide fast' -e e1 --metric wide.wall_s --at-most 10 --id c1"),
        ("rb", "criterion add 'wide good' -e e1 --metric wide.top1 --at-least 0.5 --id c2"),
        ("rb", "run add e1 wide.top1=0.7 wide.wall_s=9 --id-ignored"),
        ("rb", "run add e1 wide.top1=0.7 wide.wall_s=9"),
        ("sh", "ls .rb/experiments/e1/runs > runs.txt"),
        ("rb", "run add e1 wide.top1=0.71 wide.wall_s=12 --command two"),
        ("rb", "variant add e1 extra --role control"),
        ("rb", "retract e1/extra -m 'not needed'"),
        ("rb", "retract nope -m x"),
        ("rb", "retract e1 -m 'the whole experiment' --words 'drop it'", "", AGENT),
        ("rb", "run add e1 wide.top1=0.72 --command three"),
        ("rb", "status"),
        ("rb", "status --json"),
    ]},
    "resources": {"steps": [
        ("write", "data/train.txt", "line one\nline two\n"),
        ("rb", "init resources --no-agents"),
        ("rb", "resource add train --type dataset --name 'the train set' --version v2 --subset 'first 2 lines' --file data/train.txt --source 'made by hand' --recipe 'echo lines' --note 'tiny'"),
        ("rb", "resource add bad --type dataset --name 'bad sha' --file data/train.txt --sha256 0000000000000000000000000000000000000000000000000000000000000000"),
        ("rb", "resource add remote --type checkpoint --name 'remote ckpt' --at s3:s3://bucket/ckpt --at wandb:team/proj/ckpt:v3 --digest sha256:abc"),
        ("rb", "resource add named --type code --name 'just a name'"),
        ("rb", "resource add badat --type other --name x --at nowhere"),
        ("rb", "resource locate train runpod pod-123/data/train.txt --note 'copied'"),
        ("rb", "resource locate remote s3 s3://bucket/ckpt"),
        ("rb", "resource expose train inspected -m 'looked at the cases'"),
        ("rb", "resource expose train selected --at 2026-09-01 -m 'picked thresholds'"),
        ("rb", "resource expose train trained --at unknown -m 'pretraining'"),
        ("write", "data/train.txt", "line one\nline two changed\n"),
        ("rb", "resource add after --type dataset --name after --file data/train.txt"),
        ("rb", "resource add jsonres --type artifact --name 'json one' --json"),
        ("rb", "status"),
        ("rb", "status --json"),
    ]},
    "worktrees": {"steps": [
        ("rb", "init 'Does a wider model help?' --no-agents"),
        ("rb", "question add 'Does width help?' --id q1"),
        ("rb", "experiment add width --id e1 -q q1 --baseline base --varies model.width --note 'before the fork'"),
        ("rb", "spec set e1 base.model.width 64"),
        ("rb", "note add 'on the project'"),
        ("sh", "printf 'wt/\\n' >> .git/info/exclude && git add -A && git commit -qm start"),
        ("sh", "git worktree add -q wt/wide -b agent/wide && git worktree add -q wt/narrow -b agent/narrow"),
        ("rb", "variant add e1 wide --role candidate", "wt/wide", WIDE),
        ("rb", "spec set e1 wide.model.width 128", "wt/wide", WIDE),
        ("rb", "note add 'wide is worth a look' -e e1", "wt/wide", WIDE),
        ("rb", "criterion add 'wide beats base' -e e1 --metric change.top1 --at-least 0.01", "wt/wide", WIDE),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.76", "wt/wide", WIDE),
        ("rb", "run add e1 base.top1=0.71 wide.top1=0.78", "wt/wide", WIDE),
        ("rb", "run add e1 base.top1=0.69 wide.top1=0.75", "wt/wide", WIDE),
        ("rb", "variant add e1 narrow --role ablation", "wt/narrow", NARROW),
        ("rb", "spec set e1 narrow.model.width 32", "wt/narrow", NARROW),
        ("rb", "note add 'narrow as the floor' -e e1", "wt/narrow", NARROW),
        ("rb", "criterion add 'narrow stays usable' -e e1 --metric narrow.top1 --at-least 0.6", "wt/narrow", NARROW),
        ("rb", "run add e1 base.top1=0.70 narrow.top1=0.64", "wt/narrow", NARROW),
        ("rb", "run add e1 base.top1=0.71 narrow.top1=0.66", "wt/narrow", NARROW),
        ("rb", "status --json", "wt/narrow", NARROW),
        ("rb", "status"),
        ("sh", "cd wt/wide && git add -A && git commit -qm wide"),
        ("sh", "cd wt/narrow && git add -A && git commit -qm narrow"),
        ("rb", "status"),
        ("rb", "status e1 --json"),
        ("sh", "git merge -q --no-edit agent/wide && git merge -q --no-edit agent/narrow"),
        ("rb", "status"),
        ("rb", "show e1 --json"),
        ("rb", "compare e1"),
        ("rb", "note add 'merged: two directions, one question' -e e1"),
        ("rb", "spec set e1 wide.model.width 256"),
        ("rb", "status --json"),
    ]},
    "collision": {"steps": [
        ("rb", "init t --no-agents"),
        ("rb", "experiment add x --id e1"),
        ("rb", "experiment add y --id e2"),
        ("sh", "printf 'wt/\\n' >> .git/info/exclude && git add -A && git commit -qm start"),
        ("sh", "git worktree add -q wt/a -b agent/a && git worktree add -q wt/b -b agent/b"),
        ("rb", "run add e1 top1=0.7", "wt/a", {"RB_TEST_SEED": "one seed", "RB_TEST_NOW": "2026-10-01T12:00:01.000+00:00"}),
        ("rb", "run add e2 top1=0.6", "wt/b", {"RB_TEST_SEED": "one seed", "RB_TEST_NOW": "2026-10-01T12:00:02.000+00:00"}),
        ("sh", "cd wt/a && git add -A && git commit -qm a"),
        ("sh", "cd wt/b && git add -A && git commit -qm b"),
        ("sh", "git merge -q --no-edit agent/a && git merge -q --no-edit agent/b"),
        ("rb", "status"),
        ("rb", "status --json"),
        ("rb", "doctor"),
        ("rb", "doctor --restore"),
        ("rb", "status"),
    ]},
    "merges": {"steps": [
        ("rb", "init t --no-agents"),
        ("rb", "experiment add x --id e1 --baseline base"),
        ("rb", "spec set e1 lr 0.1"),
        ("sh", "printf 'wt/\\n' >> .git/info/exclude && git add -A && git commit -qm start"),
        ("sh", "git worktree add -q wt/a -b agent/a && git worktree add -q wt/b -b agent/b"),
        ("rb", "experiment gate e1 --if-holds 'ship it'", "wt/a", WIDE),
        ("rb", "experiment relate e1 follows_from e0-elsewhere", "wt/b", {**NARROW, "RB_TEST_NOW": "2026-10-01T12:00:05.000+00:00"}),
        ("sh", "cd wt/a && git add -A && git commit -qm a && cd ../b && git add -A && git commit -qm b"),
        ("sh", "git merge -q --no-edit agent/a && git merge -q --no-edit agent/b"),
        ("rb", "status"),
        ("rb", "spec set e1 lr 0.2", "wt/a", {**WIDE, "RB_TEST_NOW": "2026-10-01T12:00:10.000+00:00"}),
        ("rb", "spec set e1 lr 0.3", "wt/b", {**NARROW, "RB_TEST_NOW": "2026-10-01T12:00:11.000+00:00"}),
        ("sh", "cd wt/a && git add -A && git commit -qm a2 && cd ../b && git add -A && git commit -qm b2"),
        ("sh", "git merge -q --no-edit agent/a; git merge -q --no-edit agent/b; git checkout --ours .rb/experiments/e1/experiment.yaml && "
               "git add -A && git commit -qm 'merged by hand'"),
        ("rb", "status", "", LATER),
        ("rb", "status --json", "", LATER),
        ("rb", "doctor", "", LATER),
        ("rb", "doctor --adopt --why 'lr 0.2 is what ran'", "", LATER),
        ("rb", "status", "", LATER),
    ]},
    "journal": {"steps": [
        ("rb", "init t --no-agents"),
        ("rb", "experiment add x --id e1 --baseline base --candidate wide"),
        ("rb", "criterion add 'wide holds' -e e1 --metric wide.top1 --at-least 0.6 --id c1"),
        ("sh", "printf 'wt/\\n' >> .git/info/exclude && git add -A && git commit -qm start"),
        ("sh", "git worktree add -q wt/try -b agent/try"),
        ("rb", ["journal", "run", "--", "python3", "-c", 'print(\'{"metrics": {"wide.top1": 0.71}}\')'], "wt/try", WIDE),
        ("sh", "git worktree remove --force wt/try"),
        ("rb", "note add 'after the worktree went' -e e1", "", SESSION),
        ("rb", "note add 'and again' -e e1 --json", "", SESSION),
        ("rb", "reconcile"),
        ("rb", "status --json"),
    ]},
    "elsewhere": {"steps": [
        ("rb", "init t --no-agents"),
        ("rb", "question add 'does it hold?' --id q1"),
        ("rb", "experiment add x --id e1 -q q1 --baseline base --candidate wide"),
        ("sh", "printf 'wt/\\n' >> .git/info/exclude && git add -A && git commit -qm start"),
        ("sh", "git worktree add -q wt/side -b agent/side && git worktree add -q wt/live -b agent/live && git worktree add -q --detach wt/det"),
        ("rb", "experiment add 'side exp' --id e2 -q q1 --baseline a --candidate b", "wt/side", WIDE),
        ("rb", "note add 'side note' -e e1", "wt/side", WIDE),
        ("rb", "note add 'on the project, from the side'", "wt/side", WIDE),
        ("rb", "run add e1 base.top1=0.7 wide.top1=0.72", "wt/side", WIDE),
        ("sh", "cd wt/side && git add -A && git commit -qm side && cd ../.. && git worktree remove --force wt/side"),
        ("rb", "variant add e1 narrow --role ablation", "wt/live", NARROW),
        ("rb", "criterion add 'narrow ok' -e e1 --metric narrow.top1 --at-least 0.5", "wt/live", NARROW),
        ("rb", "experiment gate e1 --if-holds 'go wide'", "wt/live", NARROW),
        ("sh", "cd wt/live && git add .rb/experiments/e1/variants && git commit -qm 'one variant'"),
        ("rb", "note add 'from a detached tree'", "wt/det", LATER),
        ("rb", "status"),
        ("rb", "status --json"),
        ("rb", "status e1"),
        ("rb", "status", "wt/live", NARROW),
        ("sh", "git merge -q --no-edit agent/side"),
        ("rb", "status"),
    ]},
    "reading": {"steps": [
        ("write", "configs/train.json", TRAIN_JSON),
        ("write", "papers/paper.txt", PAPER),
        ("write", "analysis/boot.json", BOOT),
        ("write", "analysis/REPORT.md", REPORT),
        ("write", "data/eval.txt", "a\nb\n"),
        ("write", "out/model.bin", "weights\n"),
        ("rb", "init reading --no-agents"),
        ("rb", "question add 'Does width help?' --id q1"),
        ("rb", "question add 'Is it the batch?' --parent q1 --id q2"),
        ("rb", "experiment add 'wide vs base' --id e1 -q q1 --hypothesis 'wider is better' -m 'capacity' --baseline base --candidate wide --varies model.width --if-holds 'ship' --if-not 'keep'"),
        ("rb", "experiment add 'follow up' --id e2 -q q2 --purpose 'map it'"),
        ("rb", "experiment relate e2 follows_from e1 --note 'grew out of it'"),
        ("rb", "variant add e1 thin --role ablation"),
        ("rb", "metric add top1 --maximize --unit fraction --description 'top-1 accuracy' --alias acc"),
        ("rb", "metric add val_loss --minimize --unit nats"),
        ("rb", "spec set e1 optim.lr --source configs/train.json#optim.lr"),
        ("rb", "spec set e1 seed --per-run"),
        ("rb", "spec set e1 base.model.width 64 --source configs/train.json#model.width"),
        ("rb", "spec set e1 wide.model.width 128 --no-verify"),
        ("rb", "spec set e1 data.batch_size 32 --no-verify"),
        ("rb", "spec set e1 thin.data.batch_size 16 --no-verify"),
        ("rb", "spec set e1 cuda --unknown"),
        ("rb", "spec set e1 schedule cosine --cited step --cited-source papers/paper.txt"),
        ("rb", "resource add evalset --type dataset --name 'eval set' --version v1 --file data/eval.txt --url https://example.org/eval --recipe 'make eval' --note 'two lines'"),
        ("rb", "resource add ckpt --type checkpoint --name 'wide ckpt' --file out/model.bin"),
        ("rb", "resource add far --type dataset --name 'far away' --at s3:s3://bucket/far"),
        ("rb", "resource expose evalset inspected -m 'looked at the cases'"),
        ("rb", "resource expose evalset selected --at unknown -m 'picked'"),
        ("rb", "criterion add 'wide gains' -e e1 --metric change.top1 --at-least 0 --min-n 3 --noise 0.01 --id c1 -q q1 --when-met 'Width helps on this benchmark.'"),
        ("rb", "criterion add 'wide level' -e e1 --metric wide.top1 --at-least 0.7 --id c2 --words 'make it 0.7'", "", AGENT),
        ("rb", "criterion add 'cited' -e e1 --metric top1 --equals 0.83 --tolerance 0.01 --source papers/paper.txt --quote 'top1 accuracy of 0.83' --locator 'Table 2' --id c3"),
        ("rb", "criterion add 'better' -e e1 --metric top1 --subject wide --reference base --better --id c4"),
        ("rb", "criterion add 'gate' -e e1 --metric wide.top1 --at-least 0.9 --role gate --id c5"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --set seed=1 --input evalset:eval --output ckpt:checkpoint --host gpu=none --link wandb=https://example.org/r/1 --command 'python eval.py --seed 1' --artifact out/model.bin --note 'the first'"),
        ("rb", "run add e1 base.top1=0.71 wide.top1=0.76 --set seed=2 --input evalset:eval --produced-at 2026-09-30"),
        ("rb", "run add e1 base.top1=0.69 wide.top1=0.75 --set seed=3 --produced-at unknown"),
        ("rbsh", "$RB run add e1 base.top1=0.69 wide.top1=0.75 --set seed=3 --repeats $(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//')"),
        ("rb", "run add e1 thin.top1=0.60 base.top1=0.70 --set seed=4"),
        ("rbsh", "$RB analysis add e1 --compare 'wide/base:top1=0.021[0.012,0.030]' --method 'paired bootstrap over images' --unit image --units 200 --draws 10000 --reads $(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//') --input evalset:eval --id a1"),
        ("rb", "analysis add e1 --from analysis/boot.json --method 'paired bootstrap' --unit image --units 200 --command 'python boot.py' --id a2"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.021[0.012,0.030]' --source analysis/REPORT.md --quote '| wide vs base, top1 | 0.021 | [0.012, 0.030] |' --locator 'Table 1' --id a3 --supersedes a2 -m 'the report'"),
        ("rb", "analysis add e1 --compare 'wide:top1=0.81' --level 0.9 --id a4"),
        ("rb", "decide c4 accept -m 'clear enough' --revisit 'after more seeds'"),
        ("rb", "decide e1 park -m 'waiting' --words 'park it'", "", AGENT),
        ("rb", "decide e1/cuda accept -m 'read it off the image'"),
        ("rb", "decide e1/data.batch_size accept -m 'read it off the config'"),
        ("rb", "decide q2 investigate -m 'not yet'"),
        ("rb", "freeze e2 -m 'nothing to lock'"),
        ("rbsh", "$RB retract $(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//') -m 'bad seed' --scope top1"),
        ("rb", "run add e1 base.top1=0.72 wide.top1=0.77 --set seed=5"),
        ("rb", "show c1"),
        ("rb", "show c1 --json"),
        ("rb", "show c2"),
        ("rb", "show c3"),
        ("rb", "show c4 --json"),
        ("rb", "show c5"),
        ("rb", "show e1"),
        ("rb", "show e1 --json"),
        ("rb", "show e2"),
        ("rbsh", "$RB show $(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//')"),
        ("rbsh", "$RB show $(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//') --json"),
        ("rbsh", "for r in $(ls .rb/experiments/e1/runs | sed 's/[.]yaml$//'); do $RB show $r; done"),
        ("rb", "show evalset"),
        ("rb", "show evalset --json"),
        ("rb", "show ckpt"),
        ("rb", "show far"),
        ("rb", "show a1"),
        ("rb", "show a2"),
        ("rb", "show a3 --json"),
        ("rb", "show a4"),
        ("rbsh", "for d in $(ls .rb/decisions | sed 's/[.]yaml$//'); do $RB show $d; $RB show $d --json; done"),
        ("rb", "show q1"),
        ("rb", "show q1 --json"),
        ("rb", "show q2"),
        ("rb", "show top1"),
        ("rb", "show val_loss --json"),
        ("rb", "show e1/optim.lr"),
        ("rb", "show e1/cuda --json"),
        ("rb", "show e1.base.model.width"),
        ("rb", "show e1/seed"),
        ("rb", "show e1/schedule"),
        ("rb", "show e1/nothing"),
        ("rb", "show nothing"),
        ("rb", "show .rb/experiments/e1/experiment.yaml"),
        ("rb", "show .rb/experiments/e1/experiment.yaml --json"),
        ("rbsh", "$RB show .rb/experiments/e1/runs/$(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//').yaml --json"),
        ("rb", "show .rb/metrics/top1.yaml"),
        ("rb", "show .rb/questions/q2.yaml --json"),
        ("rb", "show .rb/experiments/e1/variants/thin.yaml --json"),
        ("sh", "cp .rb/metrics/val_loss.yaml loose.yaml"),
        ("rb", "show loose.yaml --json"),
        ("rb", "show loose.yaml"),
        ("write", "bad.yaml", "rb: 1\nkind: metric\nunit: nats\n"),
        ("rb", "show bad.yaml"),
        ("rb", "show bad.yaml --json"),
        ("write", "notrb.yaml", "a: yes\n"),
        ("rb", "show notrb.yaml"),
        ("write", "nokind.yaml", "rb: 1\nkind: thing\n"),
        ("rb", "show nokind.yaml --json"),
        ("sh", "sed -i 's/unit: fraction/unit: percent/' .rb/metrics/top1.yaml"),
        ("rb", "show top1"),
        ("rb", "show top1 --json"),
        ("rb", "show c2"),
        ("rb", "compare e1"),
        ("rb", "compare e1 --json"),
        ("rb", "compare"),
        ("rb", "compare e2"),
        ("rb", "log"),
        ("rb", "log -n 3 --json"),
        ("rb", "log e1"),
        ("rb", "log e1/cuda"),
        ("rb", "log -n -2"),
        ("rb", "context"),
        ("rb", "context --json"),
        ("rb", "status"),
        ("rb", "page"),
        ("rb", "page --out shared/one.html"),
        ("sh", "mkdir -p shared"),
        ("rb", "page --out shared/one.html --json"),
        # a fixed grid of widths with seeds as samples, criteria that select the runs they read, a declared reversal,
        # and a call on the question they are linked to
        ("rb", "experiment add 'width grid' --id e3 -q q1 --baseline base --candidate wide"),
        ("rb", "spec set e3 seed --per-run --sample-factor"),
        ("rb", "spec set e3 width --per-run"),
        ("rb", "spec set e3 width --design-factor --why 'a fixed grid of widths'"),
        ("rb", "spec set e3 loops --per-run"),
        ("rb", "criterion add 'wide lowers val_loss at 512' -e e3 --metric change.val_loss --at-most 0 --where width=512 --id c6 -q q1 --when-met 'Width lowers val_loss at 512.'"),
        ("rb", "criterion add 'wide lowers val_loss across the grid' -e e3 --metric change.val_loss --at-most 0 --id c7"),
        ("rb", "criterion add 'more loops lower val_loss for wide' -e e3 --metric val_loss --subject wide --reference wide --where width=512 --subject-where loops=8 --reference-where loops=6 --at-most 0 --id c8"),
        ("rb", "run add e3 base.val_loss=1.0 wide.val_loss=0.9 --set seed=1 --set width=512 --set loops=8"),
        ("rb", "run add e3 base.val_loss=1.0 wide.val_loss=0.95 --set seed=2 --set width=512 --set loops=6"),
        ("rb", "run add e3 base.val_loss=1.0 wide.val_loss=1.02 --set seed=1 --set width=256 --set loops=8"),
        ("rb", "criterion relate c7 reverses c6 --orientation same --why 'the same arms across the grid and at one width'"),
        ("rb", "decide q1 continue -m 'on the linked readings'"),
        ("rb", "show c6"),
        ("rb", "show c7 --json"),
        ("rb", "show c8"),
        ("rb", "show q1"),
        ("rb", "status"),
    ]},
    "upkeep": {"steps": [
        ("rb", "init upkeep --no-agents"),
        ("rb", "question add 'Is it fast?' --id q1"),
        ("rb", "experiment add 'fast vs slow' --id e1 -q q1 --baseline slow --candidate fast"),
        ("rb", "metric add wall_s --minimize --unit s"),
        ("rb", "resource add data1 --type dataset --name 'data one'"),
        ("rb", "run add e1 slow.wall_s=10 fast.wall_s=7 --input data1:eval"),
        ("rb", "fmt --check"),
        ("rb", "fmt --check --json"),
        ("sh", "printf '# keep this comment\\n' >> .rb/questions/q1.yaml && sed -i 's/^  /    /' .rb/experiments/e1/experiment.yaml"),
        ("rb", "fmt --check"),
        ("rb", "fmt --check .rb/questions"),
        ("rb", "fmt .rb/experiments/e1/experiment.yaml"),
        ("rb", "fmt"),
        ("rb", "fmt --json"),
        ("rb", "status"),
        ("write", "loose/one.yaml", "b:   2\na: 1\n"),
        ("write", "loose/two.yaml", "a: yes\n"),
        ("write", "loose/deep/three.yaml", "x: [1, 2]\n"),
        ("sh", "printf 'a: \\377\\n' > loose/bin.yaml && mkdir -p loose/dir.yaml"),
        ("rb", "fmt loose"),
        ("rb", "fmt loose --json"),
        ("rb", "fmt loose/missing.yaml"),
        ("rb", "doctor"),
        ("rb", "doctor --json"),
        ("sh", "sed -i 's/text: Is it fast?/text: Is it faster?/' .rb/questions/q1.yaml"),
        ("rb", "doctor"),
        ("rb", "doctor --restore --adopt"),
        ("rb", "doctor --adopt"),
        ("rb", "doctor --adopt --why 'clarify the question' --seen nowhere"),
        ("rb", "doctor --adopt --why 'clarify the question' --seen .rb/questions/q1.yaml@0000"),
        ("rb", "doctor --adopt --why 'clarify the question' --words 'keep my wording'", "", AGENT),
        ("rb", "doctor"),
        ("sh", "sed -i 's/unit: s/unit: ms/' .rb/metrics/wall_s.yaml"),
        ("rb", "doctor --restore"),
        ("rb", "doctor --restore"),
        ("sh", "printf '<<<<<<< HEAD\\nkind: question\\n=======\\n>>>>>>> other\\n' > .rb/questions/q2.yaml"),
        ("rb", "doctor"),
        ("sh", "rm .rb/questions/q2.yaml && printf 'rb: 1\\nkind: question\\nid: q3\\n' > .rb/questions/q3.yaml"),
        ("rb", "doctor"),
        ("rb", "doctor --json"),
        ("sh", "rm .rb/questions/q3.yaml && printf 'rb: 1\\nkind: question\\nid: q4\\ntext: [unclosed\\n' > .rb/questions/q4.yaml"),
        ("rb", "doctor"),
        ("sh", "rm .rb/questions/q4.yaml && rm .rb/resources/data1.yaml"),
        ("rb", "doctor"),
        ("rb", "doctor --restore"),
        ("rb", "doctor"),
        ("sh", "printf 'rb: 9\\nkind: question\\nid: q5\\ntext: from the future\\n' > .rb/questions/q5.yaml"),
        ("rb", "doctor"),
        ("sh", "rm .rb/questions/q5.yaml"),
        ("rb", "doctor"),
    ]},
    "compile": {"steps": [
        # an agent rb sets up under a folder of its own: a file rb writes at the root would be watched with its real time
        ("write", ".cursor/rules/house.mdc", "Be careful.\n"),
        ("write", "results/warmup/README.md", FOLDER_README),
        ("write", "results/warmup/metrics.json", '{"top1": 0.74, "top5": 0.91}\n'),
        ("write", "results/warmup/audit.json", '{"passed": false, "status": "fail", "why": "seed 2 missing"}\n'),
        ("write", "results/warmup/manifest.json", '{"sources": {"scripts/train.py": "0000000000000000000000000000000000000000000000000000000000000000"}}\n'),
        ("write", "results/other/report.md", "# Other\n\nNothing to see.\n"),
        ("write", "REPRODUCE.md", REPRODUCE),
        ("write", "scripts/audit_warmup.py", AUDIT_PY),
        ("write", "scripts/train.py", TRAIN_PY),
        ("rb", "reconcile"),
        ("rb", "reconcile --json"),
        ("rb", "status"),
        ("sh", "git add -A && git commit -qm start"),
        ("rb", "reconcile"),
        ("rb", "reconcile --apply"),
        ("rb", "status"),
        ("rb", "show warmup"),
        ("rb", "reconcile"),
        ("rb", "criterion add 'top1 holds' -e warmup --metric top1 --at-least 0.7 --id c1"),
        ("write", "results/warmup/seed2/metrics.json", '{"top1": 0.75, "top5": 0.92}\n'),
        ("rb", "journal scan"),
        ("rb", "journal scan --json"),
        ("rb", "reconcile"),
        ("rb", "reconcile --json"),
        ("rb", "reconcile --apply"),
        ("rb", "status"),
        ("rb", ["journal", "run", "--", "python3", "scripts/audit_warmup.py", "results/warmup"]),
        ("rb", ["journal", "run", "--json", "--", "python3", "-c", 'print("{\\"metrics\\": {\\"top1\\": 0.76, \\"loss\\": 1.5}}")']),
        ("write", "scripts/audit_bad.py", AUDIT_FAIL_PY),
        ("rb", ["journal", "run", "--", "python3", "scripts/audit_bad.py"]),
        ("rb", ["journal", "run", "--", "no-such-command-here"]),
        ("rb", "journal run"),
        ("rb", "journal"),
        ("rb", "journal -n 2 --json"),
        ("rb", "journal -n -3"),
        ("rb", "reconcile"),
        ("write", "results/warmup/README.md", FOLDER_README.replace("0.74 against 0.73", "0.75 against 0.73")),
        ("rb", "reconcile"),
        ("rb", "reconcile --apply --json"),
        ("rb", "context"),
        ("rb", "page"),
    ]},
    "unrecorded": {"steps": [
        ("write", "README.md", "# The project's own title\n\nWords.\n"),
        ("rb", "reconcile"),
        ("rb", "reconcile --json"),
        ("write", "outputs/sweep/results.json", '{"loss": 0.5}\n'),
        ("write", "scripts/check_sweep.py", "print('ok')\n"),
        ("write", "RUNNING.md", "# How to run\n\n```\npython3 scripts/check_sweep.py\n```\n"),
        ("rb", "reconcile"),
        ("rb", "reconcile --apply"),
        ("rb", "status"),
        ("rb", "reconcile --apply"),
    ]},
    "info": {"steps": [
        ("rb", "version"),
        ("rb", "version --json"),
        ("rb", "--version"),
        ("rb", "docs"),
        ("rb", "docs --errors"),
        ("rb", "docs --errors --json"),
        ("rb", "schema all"),
        ("rb", "schema ir"),
        ("rb", "schema ir --json"),
        ("rb", "schema run"),
        ("rb", "schema finding --json"),
        ("rb", "schema envelope"),
        ("rb", "schema nothing"),
        ("rb", "env check"),
        ("rb", "init info --no-agents"),
        ("rb", "env check"),
        ("rb", "env check --json"),
        ("rb", "experiment add e --id e1 --baseline base --candidate wide"),
        ("rb", "spec set e1 env.python 2.7 --no-verify"),
        ("rb", "spec set e1 env.rb 0.0.0 --no-verify"),
        ("rb", "spec set e1 env.cuda --unknown"),
        ("rb", "run add e1 base.top1=0.7 wide.top1=0.71"),
        ("rb", "run add e1 base.top1=0.7 wide.top1=0.72 --command two"),
        ("rb", "run add e1 base.top1=0.7 wide.top1=0.73 --command three", "", OTHER_MACHINE),
        ("rb", "env check", "", OTHER_MACHINE),
        ("sh", "sed -i 's/^    python: .*/    python: 2.7.18/' .rb/experiments/e1/runs/*.yaml"),
        ("rb", "env check"),
        ("rb", "env check --json"),
        ("rb", "env"),
    ]},
    "share": {"steps": [
        ("write", "configs/train.json", TRAIN_JSON),
        ("write", "papers/paper.txt", PAPER),
        ("write", "papers/gone.txt", PAPER),
        ("write", "analysis/REPORT.md", REPORT),
        ("write", "data/eval.txt", "a\nb\n"),
        ("rb", "init share --no-agents"),
        ("rb", "question add 'Does width help?' --id q1"),
        ("rb", "experiment add 'wide vs base' --id e1 -q q1 --baseline base --candidate wide --hypothesis 'wider wins'"),
        ("rb", "metric add top1 --maximize"),
        ("rb", "spec set e1 optim.lr --source configs/train.json#optim.lr"),
        ("rb", "spec set e1 schedule cosine --cited step --cited-source papers/gone.txt"),
        ("rb", "criterion add 'cited' -e e1 --metric top1 --equals 0.83 --tolerance 0.01 --source papers/paper.txt --quote 'top1 accuracy of 0.83' --id c1"),
        ("rb", "criterion add 'wide gains' -e e1 --metric change.top1 --at-least 0 --id c2"),
        ("rb", "resource add evalset --type dataset --name 'eval set' --file data/eval.txt"),
        ("rb", "resource add far --type checkpoint --name 'far ckpt' --at s3:s3://bucket/ckpt --recipe 'train it again'"),
        ("rb", "resource add named --type code --name 'just a name'"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --input evalset:eval --output far:checkpoint"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.021[0.012,0.030]' --source analysis/REPORT.md --quote '| wide vs base, top1 | 0.021 | [0.012, 0.030] |' --id a1"),
        ("sh", "rm papers/gone.txt && git add -A && git commit -qm record"),
        ("rb", "export"),
        ("rb", "export --with-record --json"),
        ("rb", "status", "rb-export/record"),
        ("rb", "export --zip"),
        ("rb", "export --out bundles/b1 --no-repo"),
        ("rb", "export --out .rb/inside"),
        ("sh", "mkdir -p notours && echo keep > notours/file.txt"),
        ("rb", "export --out notours"),
        ("sh", "git remote add origin https://someone:secret@example.org/team/repo.git?x=1"),
        ("rb", "export --url https://example.org/share --out unaddressed"),
        ("rb", "export --with-record --url https://example.org/share --out published"),
        ("rb", "export --with-record --url https://example.org/share.zip --zip --out pub.zip --json"),
        ("rb", "export --no-repo --out plain"),
    ]},
    "fork": {"steps": [
        ("sh", "rm -rf ../elsewhere"),
        ("write", "proj/README.md", "# Fork me\n"),
        ("write", "proj/configs/train.json", TRAIN_JSON),
        ("write", "proj/papers/paper.txt", PAPER),
        ("rb", "init fork --no-agents", "proj"),
        ("rb", "question add 'Does width help?' --id q1", "proj"),
        ("rb", "question add 'And depth?' --parent q1 --id q2", "proj"),
        ("rb", "experiment add 'wide vs base' --id e1 -q q1 --baseline base --candidate wide --varies model.width --hypothesis 'wider wins' --if-holds ship --note 'kept'", "proj"),
        ("rb", "experiment relate e1 extends exp_01PARENTPARENTPARENT --note 'grew from it'", "proj"),
        ("rb", "metric add top1 --maximize --alias acc --description 'top-1'", "proj"),
        ("rb", "spec set e1 optim.lr --source configs/train.json#optim.lr", "proj"),
        ("rb", "spec set e1 seed --per-run", "proj"),
        ("rb", "spec set e1 cuda --unknown", "proj"),
        ("rb", "spec set e1 wide.model.width 128 --no-verify", "proj"),
        ("rb", "variant add e1 gone --role ablation", "proj"),
        ("rb", "retract e1/gone -m 'not needed'", "proj"),
        ("rb", "criterion add 'cited' -e e1 --metric top1 --equals 0.83 --tolerance 0.01 --source papers/paper.txt --quote 'top1 accuracy of 0.83' --id c1", "proj"),
        ("rb", "criterion add 'wide gains' -e e1 --metric change.top1 --at-least 0 --min-n 3 --noise 0.01 --id c2", "proj"),
        ("rb", "criterion add 'dropped' -e e1 --metric wide.top1 --at-least 0.5 --id c3", "proj"),
        ("rb", "retract c3 -m 'not the claim'", "proj"),
        ("rb", "resource add data1 --type dataset --name 'data one' --at local:/abs/data --at local:data/rel --at s3:s3://b/d --sha256 0000000000000000000000000000000000000000000000000000000000000000", "proj"),
        ("rb", "note add 'a project note'", "proj"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --set seed=1 --input data1:eval", "proj"),
        ("rb", "decide e1 continue -m 'keep going'", "proj"),
        ("rb", "export --with-record --out ../bundle", "proj"),
        ("rb", "export --with-record --zip --out ../bundle.zip", "proj"),
        ("rb", "export --with-record --url https://example.org/fork --out ../pub", "proj"),
        ("rb", "export --out ../page", "proj"),
        ("rb", "clone page cloned5"),
        ("rb", "clone bundle cloned"),
        ("rb", "status", "cloned"),
        ("rb", "show e1 --json", "cloned"),
        ("rb", "log", "cloned"),
        ("rb", "clone bundle.zip cloned2 --json"),
        ("rb", "clone pub cloned3"),
        ("rb", "clone pub/bundle.zip cloned4"),
        ("rb", "clone bundle cloned"),
        ("rb", "clone bundle proj/inner"),
        ("sh", "mkdir -p full && echo x > full/f"),
        ("rb", "clone bundle full"),
        ("rb", "clone nothing"),
        ("write", "bad.zip", "not a zip\n"),
        ("rb", "clone bad.zip x1"),
        ("sh", "cp -r bundle tampered && echo changed >> tampered/README.md"),
        ("rb", "clone tampered x2"),
        ("sh", "cp -r bundle newer && sed -i 's/\"version\": 2/\"version\": 3/' newer/manifest.json"),
        ("rb", "clone newer x3"),
        ("sh", "cp -r bundle other && sed -i 's/\"format\": \"rb-bundle\"/\"format\": \"other\"/' other/manifest.json"),
        ("rb", "clone other x4"),
        ("rb", "clone bundle ../elsewhere/c1"),
        ("rb", "clone bundle"),
        ("rb", "export --with-record --out ../forked", "fork"),
        ("rb", "status", "fork"),
    ]},
    "exhaust": {"steps": [
        ("write", "report.md", EXHAUST_REPORT),
        ("write", "notes/plain.txt", "first line says something\n\nmore\n"),
        ("write", "notes/empty.md", "\n\n"),
        ("write", "data/metrics.json", '{"top1": 0.74, "acc": 0.74, "other": 3}\n'),
        ("write", "data/unknown.json", '{"zz": 1.5}\n'),
        ("write", "data/table.csv", "seed, top1 ,top5\n1,0.7,0.9\n"),
        ("write", "data/tabs.tsv", "a\tb\n"),
        ("write", "data/run.py", "print(1)\n"),
        ("write", "data/.hidden.md", "# hidden\n"),
        ("write", "data/__pycache__/x.md", "# cache\n"),
        ("write", "data/blob.bin", "xx"),
        ("rb", "compile report.md data"),
        ("rb", "compile"),
        ("rb", "init exhaust --no-agents"),
        ("rb", "question add 'Does width help?' --id q1"),
        ("rb", "experiment add 'wide vs base' --id e1 -q q1 --baseline base --candidate wide --hypothesis 'wider wins'"),
        ("rb", "metric add top1 --maximize --alias acc"),
        ("rb", "spec set e1 lr 0.0003 --no-verify"),
        ("rb", "criterion add 'wide reaches 0.7' -e e1 --metric wide.top1 --at-least 0.7 --id c1"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74 --command again"),
        ("rb", "analysis add e1 --compare 'wide/base:top1=0.04[0.01,0.07]' --id a1"),
        ("rb", "compile report.md data notes"),
        ("rb", "compile report.md --json"),
        ("rb", "compile"),
        ("rb", "compile nothing.md data/metrics.json"),
        ("rb", "experiment add 'second' --id e2 --baseline base --candidate wide"),
        ("rb", "criterion add 'also top1' -e e2 --metric wide.top1 --at-least 0.5 --id c2"),
        ("rb", "compile data/metrics.json data/unknown.json"),
        ("write", "data/metrics.json", '{"base.top1": 0.70, "wide.top1": 0.74}\n'),
        ("rb", "compile data/metrics.json"),
    ]},
    "tour": {"steps": [
        ("rb", "tour"),
        ("rb", "tour"),
        ("rb", "tour --json", "rb-tour"),
        ("rb", "tour second --json"),
        ("rb", "status", "second"),
        ("sh", "mkdir -p third && echo x > third/f"),
        ("rb", "tour third"),
    ]},
    # rb open around its serving (rust/tests_py/diff_open.py asks the server itself): no project, an agent session, ports
    # out of range or taken, and Ctrl-C, after which it says nothing more. The port a run gets is written as PORT. And
    # rb ui wherever it does not open: no project, an agent session, no terminal.
    "open": {"steps": [
        ("rb", "open"),
        ("rb", "open --json"),
        ("rb", "ui"),
        ("rb", "ui --json"),
        ("rb", "init t --no-agents"),
        ("rb", "open", "", {"CLAUDECODE": "1"}),
        ("rb", "open --json", "", {"CLAUDECODE": "1"}),
        # the screen too is the user's, in a terminal: an agent session and a pipe are each told so, and where to look
        ("rb", "ui", "", {"CLAUDECODE": "1"}),
        ("rb", "ui --json", "", {"CLAUDECODE": "1"}),
        ("rb", "ui"),
        ("rb", "ui --json"),
        ("rb", "ui extra"),
        ("rb", ""),
        ("rb", "open --port 70000"),
        ("rb", "open --port -1 --json"),
        ("rb", "open --port abc"),
        ("write", "serve.py", OPEN_PY),
        ("rbsh", "python3 serve.py taken"),
        ("rbsh", "python3 serve.py taken --json"),
        ("rbsh", "python3 serve.py stop"),
        ("rbsh", "python3 serve.py stop --json"),
    ]},
    "mcp": {"steps": [
        ("write", "configs/train.json", TRAIN_JSON),
        ("rb", "init mcp --no-agents"),
        ("rb", "question add 'Does width help?' --id q1"),
        ("rb", "experiment add 'wide vs base' --id e1 -q q1 --baseline base --candidate wide --varies model.width"),
        ("rb", "metric add top1 --maximize"),
        ("rb", "criterion add 'wide holds' -e e1 --metric wide.top1 --at-least 0.6 --id c1"),
        ("rb", "run add e1 base.top1=0.70 wide.top1=0.74"),
        ("write", "conversation.jsonl", MCP_CONVERSATION),
        ("write", "odd.jsonl", MCP_ODD),
        ("write", "nameless.jsonl", MCP_NAMELESS),
        ("rbsh", "$RB mcp < conversation.jsonl"),
        ("rbsh", "$RB mcp < odd.jsonl", SESSION),
        ("rbsh", "$RB mcp --agent 'Bot One' < nameless.jsonl"),
        ("rbsh", "$RB mcp < /dev/null"),
        ("rb", "mcp --bogus"),
        ("rb", "mcp --agent"),
        ("rb", "mcp --json"),
        ("rb", "mcp extra"),
        ("rb", "mcp --help"),
        ("rb", "status"),
        ("rb", "log -n 60"),
    ]},
    # rb journal over what a journal may hold when something else wrote it: numbers and lists where text belongs, no
    # time seen, times it cannot compare. Each such entry is passed over and counted; --json gives it as it is.
    "journal-odd": {"steps": [
        ("rb", "init t --no-agents"),
        ("write", ".rb/journal/journal.jsonl", JOURNAL_ODD),
        ("rb", "journal"),
        ("rb", "journal --json"),
        ("rb", "journal -n 2"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "2026-10-01T12:00:00+00:00", "op": "file", "path": "a", "written": "2026-10-01T11:00:00"}\n'),
        ("rb", "journal"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "yesterday", "op": "file", "path": "a", "written": "2026-10-01T11:00:00"}\n'),
        ("rb", "journal"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "2026-10-01T12:00:00", "op": "file", "path": "a", "written": 5}\n'),
        ("rb", "journal --json"),
        ("write", ".rb/journal/journal.jsonl", '{"at": null, "op": "file", "path": "a", "written": "2026-10-01T11:00:00"}\n'),
        ("rb", "journal"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "2026-10-01T12:00:00", "op": "file", "path": "a", "change": null}\n'),
        ("rb", "journal"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "2026-10-01T12:00:00", "op": "file", "path": "a", "change": ["new"]}\n'),
        ("rb", "journal"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "2026-10-01T12:00:00", "op": "git", "commit": 5}\n'),
        ("rb", "journal"),
        ("write", ".rb/journal/journal.jsonl", '{"at": "2026-10-01T12:00:00", "op": "git", "commit": {"a": 1}}\n'),
        ("rb", "journal"),
    ]},
    # rb show <file> on record files of every kind of wrong: each problem said where it is and what is wrong there.
    "showfile": {"steps": [
        ("rb", "init t --no-agents"),
        ("rb", "experiment add x --id e1 --baseline base --candidate wide"),
        ("rb", "criterion add 'wide holds' -e e1 --metric wide.top1 --at-least 0.6 --id c1"),
        ("write", "m.yaml", "rb: 1\nkind: metric\nid: m\nunit: 5\ndirection: sideways\naliases: a\n"),
        ("rb", "show m.yaml"),
        ("rb", "show m.yaml --json"),
        ("write", "crit.yaml", CRITERIA_ODD),
        ("rb", "show crit.yaml"),
        ("rb", "show crit.yaml --json"),
        ("write", "kinds.yaml", "rb: 1\nkind:\n  - metric\n"),
        ("rb", "show kinds.yaml"),
        ("write", "nokind.yaml", "rb: 1\nid: x\n"),
        ("rb", "show nokind.yaml --json"),
        ("write", "truthy.yaml", "rb: true\nkind: note\nid: n1\ntext: hi\nby: human:t\nat: '2026-10-01T12:00:00.000+00:00'\n"),
        ("rb", "show truthy.yaml --json"),
        ("write", "list.yaml", "- rb: 1\n- kind: metric\n"),
        ("rb", "show list.yaml --json"),
        ("write", "q.json", '{"rb": 1, "kind": "question", "id": "q1", "text": "does it?", "created_by": "human:t", "created_at": "2026-10-01T12:00:00.000+00:00"}\n'),
        ("rb", "show q.json"),
        ("write", "exp.yaml", "rb: 1\nkind: experiment\nid: e\ntitle: t\nuid: u\nvariants:\n  - name: 1\n  - name: a\n    role: baseline\nsettings: 5\n"),
        ("rb", "show exp.yaml --json"),
        ("write", "rule.yaml", "rb: 1\nkind: criterion\nid: c\nexperiment: e\nstatement: s\nmetric: m\ncomparator: at_most\ncreated_by: human:t\ncreated_at: x\n"),
        ("rb", "show rule.yaml"),
        ("write", "broken.yaml", "rb: 1\nkind: metric\n  id: [\n"),
        ("rb", "show broken.yaml"),
        ("sh", "printf 'rb: 1\\nkind: note\\ntext: \\377\\n' > latin.yaml"),
        ("rb", "show latin.yaml"),
        ("rb", "show latin.yaml --json"),
        ("write", "nan.yaml", "rb: 1\nkind: metric\nid: m\nscale: .nan\n"),
        ("rb", "show nan.yaml"),
    ]},
    # rb bridge, which rb's Python package (the SDK, the terminal UI) asks: every op, in the order a script and the user's
    # screen would ask them, with ids only rb knows put in by the shell; then everything that is not a request.
    "bridge": {"steps": [
        ("write", "configs/train.yaml", TRAIN_YAML),
        ("write", "paper.txt", PAPER),
        ("write", "ckpt/wide.pt", "weights\n"),
        ("sh", "git add . && git commit -qm init"),
        ("write", "req/declare.jsonl", BRIDGE_DECLARE),
        ("write", "req/runs.jsonl", BRIDGE_RUNS),
        ("write", "req/read.jsonl", BRIDGE_READ),
        ("write", "req/screen.jsonl", BRIDGE_SCREEN),
        ("write", "req/refused.jsonl", BRIDGE_REFUSED),
        ("rbsh", "sed \"s#@ROOT@#$PWD#g; s#\\\"HEAD\\\"#\\\"$(git rev-parse HEAD)\\\"#g\" req/declare.jsonl | $RB bridge"),
        ("write", "out/seed1/metrics.json", '{"base.top1": 0.71, "wide.top1": 0.74}\n'),
        ("write", "out/seed1/config.yaml", TRAIN_YAML),
        ("rbsh", "sed \"s#@ROOT@#$PWD#g; s#\\\"HEAD\\\"#\\\"$(git rev-parse HEAD)\\\"#g\" req/runs.jsonl | $RB bridge"),
        ("rbsh", "sed \"s#@ROOT@#$PWD#g; s#@RUN@#$(ls .rb/experiments/e1/runs | head -1 | sed 's/[.]yaml$//')#g; "
                 "s#@RUN2@#$(ls .rb/experiments/e1/runs | sed -n 2p | sed 's/[.]yaml$//')#g\" req/read.jsonl | $RB bridge"),
        ("write", "out/extra.json", '{"base.top1": 0.7, "wide.top1": 0.76}\n'),
        ("write", "out/loose.json", '{"unheard_of": 1.5}\n'),
        ("rbsh", "sed \"s#@ROOT@#$PWD#g; s#@FOUND@#j$(sha256sum out/extra.json | cut -c1-10)#g; s#@KEY@#$(sha256sum out/extra.json | cut -c1-64)#g; "
                 "s#@RUN2@#$(ls .rb/experiments/e1/runs | sed -n 2p | sed 's/[.]yaml$//')#g\" req/screen.jsonl | $RB bridge"),
        # a zip's bytes are its writer's (the harness compares its entries), and a run's receipt hashes the untracked files
        ("sh", "rm exported.zip"),
        ("rbsh", "sed \"s#@ROOT@#$PWD#g\" req/refused.jsonl | $RB bridge"),
        ("rbsh", "$RB bridge < /dev/null"),
        ("rb", "bridge extra"),
        ("rb", "bridge --help"),
        ("rb", "bridge --json"),
        ("rb", "status"),
        ("rb", "log -n 200"),
        ("rb", "doctor"),
    ]},
    "before": {"env": {"RB_TEST_NOW": "2026-10-02T12:00:00.000+00:00"}, "steps": [
        ("copy", "tests/fixtures/shared-files-record/.rb", ".rb"),
        ("sh", "git add -A && git commit -qm before"),
        ("rb", "status"),
        ("rb", "show e1 --json"),
        ("rb", "note add 'third note' -e e1"),
        ("rb", "status --json"),
        ("rb", "note add 'another on the project'"),
        ("rb", "criterion add 'narrow holds' -e e1 --metric narrow.top1 --at-least 0.6 --id c3"),
        ("rb", "variant add e1 half --role ablation"),
        ("rb", "show c2"),
        ("rb", "log -n 4 --json"),
    ]},
}
