"""Every tool `rb mcp` lists takes a call (round mcp-1, Nov 15 2026): a tool whose command words, argument names or
switches drift from rb's command line refuses every call, and a test that only lists the tools never sees it.

`record(root)` makes a small INT8 record to call the tools on. `arguments(tool)` builds a valid call from the tool's own
schema: its required arguments, each from the table of example values below (by tool and argument, else by argument,
else by type), and `dry_run` where the tool takes it, so a write is checked and nothing is written. `one_of_each(tool)`
gives the calls that add each argument the schema lists, one at a time (each value of an enum), which the command line
must parse. `against_the_parser(tool, parser)` reads the schema against rb's declared command line (rust/src/cli_parser.py):
the words name a command, each option names a flag of it with the same kind of value, the positionals come in its
order, and what it requires the schema requires.

A tool added later is covered as it is listed: it needs a line in EXAMPLES only where no argument name, nor its type,
gives a value its command takes."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

# The ids the record holds, and a value for each argument a valid call needs: by (tool, argument) first, then by
# argument. A tool's own lines give what makes its call do its job on this record (a new id where it adds one).
BY_TOOL = {
    ("show", "id"): "int8",
    ("log", "id"): "int8",
    ("files", "path"): "configs",
    ("question_add", "id"): "q-new",
    ("question_move", "question"): "q-sub",
    ("question_move", "top"): True,
    ("experiment_add", "id"): "e-new",
    ("experiment_file", "experiment"): "int8b",
    ("experiment_relate", "experiment"): "int8b",
    ("experiment_relate", "relation"): "follows_from",
    ("experiment_gate", "if_holds"): "INT8 ships",
    ("variant_add", "name"): "fp16",
    ("variant_add", "role"): "ablation",
    ("metric_add", "name"): "top1",
    ("spec_set", "name"): "optim.lr",
    ("spec_set", "source"): "configs/eval.yaml#optim.lr",
    ("spec_vary", "names"): ["optim.lr"],
    ("criterion_add", "metric"): "change.val_loss",
    ("criterion_add", "at_most"): 0.1,
    ("criterion_file", "question"): "q-other",
    ("criterion_relate", "target"): "c-loss2",
    ("criterion_relate", "orientation"): "same",
    ("analysis_add", "compare"): ["int8/fp32:val_loss=0.02[0.01,0.03]"],
    ("analysis_add", "method"): "paired bootstrap over images",
    ("run_add", "metrics"): {"int8.val_loss": 0.987, "fp32.val_loss": 0.961},
    ("run_add", "set"): {"seed": 2},
    ("run_add", "produced_at"): "2026-11-01T10:00:00Z",
    ("resource_add", "id"): "imagenet-val",
    ("resource_locate", "provider"): "url",
    ("resource_locate", "ref"): "https://data.example.org/holdout-v2",
    ("resource_expose", "how"): "inspected",
    ("freeze", "experiment"): "int8b",
    ("decide", "subject"): "c-loss",
    ("decide", "action"): "accept",
}
BY_NAME = {
    "experiment": "int8", "question": "q-acc", "criterion": "c-loss", "id": "holdout-v2", "subject": "c-loss",
    "target": "int8", "relation": "reverses", "title": "INT8 ResNet-50 on ImageNet", "text": "Does INT8 cost top-1 on ImageNet?",
    "statement": "INT8 adds at most 0.1 validation loss", "name": "ImageNet-1k validation", "type": "dataset",
    "why": "configs/eval.yaml:2", "words": "keep it", "value": 0.1, "names": ["precision"], "licence": "CC-BY-4.0",
    "model": "example-model-1", "provider": "example", "url": "https://data.example.org/holdout-v2",
}
# arguments the schema lists that rb mcp reads itself rather than pass on as a flag (rust/src/mcp.rs): `all` keeps a
# long answer whole (tool_result), `metrics` becomes a run's name=value words (argv_for), `direction` its switch
MCP_OWN = {"all": ("status", "files", "reconcile"), "metrics": ("run_add",)}
# and the positional each of those words fills, after the tool's own
MCP_OWN_POSITIONAL = {"run_add": ["pairs"]}


def rb(root: Path, *argv: str, env: dict) -> str:
    r = subprocess.run([*_command(), *argv], cwd=root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode in (0, 1), (argv, r.stdout, r.stderr)
    return r.stdout


def _command() -> list:
    from rbtest import command
    return command()


def record(root: Path, env: dict) -> dict:
    """The record the tools are called on, written as the user (env): two questions and a sub-question; int8 (fp32
    against int8, a per-run seed, a rule c-loss, a run, a note) and int8b (a rule c-loss2 on the same comparison); a
    metric; a dataset. Returns the id rb drew for the run."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "configs").mkdir()
    (root / "configs" / "eval.yaml").write_text("optim:\n  lr: 0.1\neval:\n  batch_size: 256\n", encoding="utf-8")
    for cmd in (["init", "-q"], ["config", "user.email", "ana@example.com"], ["config", "user.name", "ana"], ["add", "."],
                ["commit", "-qm", "start"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)
    run = lambda *a: rb(root, *a, env=env)  # noqa: E731
    run("init", "INT8 ResNet-50", "--no-agents")
    run("question", "add", "Does INT8 cost accuracy on the held-out set?", "--id", "q-acc")
    run("question", "add", "Does INT8 run faster?", "--id", "q-other")
    run("question", "add", "Does the calibration set matter?", "--id", "q-sub", "--parent", "q-acc")
    run("metric", "add", "val_loss", "--minimize", "--unit", "nats")
    for exp in ("int8", "int8b"):
        q = "q-acc" if exp == "int8" else "q-other"
        run("experiment", "add", f"INT8 vs FP32 ({exp})", "--id", exp, "-q", q, "--baseline", "fp32", "--candidate", "int8",
            "--varies", "precision", "--hypothesis", "INT8 keeps validation loss within 0.05 of FP32")
        run("spec", "set", exp, "fp32.precision", "fp32", "--source", "note:the baseline")
        run("spec", "set", exp, "int8.precision", "int8", "--source", "note:the candidate")
        run("spec", "set", exp, "seed", "--per-run")
    run("criterion", "add", "INT8 adds at most 0.05 validation loss", "-e", "int8", "-q", "q-acc", "--metric", "change.val_loss",
        "--at-most", "0.05", "--id", "c-loss")
    run("criterion", "add", "INT8 adds at most 0.05 validation loss, again", "-e", "int8b", "--metric", "change.val_loss",
        "--at-most", "0.05", "--id", "c-loss2")
    run("resource", "add", "holdout-v2", "--type", "dataset", "--name", "held-out images, v2", "--url", "https://data.example.org/holdout-v2")
    made = json.loads(run("run", "add", "int8", "int8.val_loss=0.99", "fp32.val_loss=0.96", "--set", "seed=1", "--produced-at",
                          "2026-11-01T09:00:00Z", "--json"))
    run("note", "add", "the held-out set is not in the pretraining data", "-e", "int8")
    return {"run": made["data"]["object"]["id"]}


def example(tool: dict, name: str, ids: dict) -> Any:
    """A value for one argument of a tool: the table's, else one of its type (an enum's first value)."""
    key = (tool["name"], name)
    if key in BY_TOOL:
        return BY_TOOL[key]
    if tool["name"] == "retract" and name == "subject":
        return ids["run"]      # a session takes back a run; the user's own note or call stays theirs
    if name in BY_NAME:
        return BY_NAME[name]
    prop = tool["props"][name]
    if prop.get("enum"):
        return prop["enum"][0]
    kind = prop.get("type")
    kind = kind[0] if isinstance(kind, list) else kind
    return {"boolean": True, "integer": 3, "number": 0.5, "array": ["x"], "object": {"k": "v"}}.get(kind, "x")


def arguments(tool: dict, ids: dict) -> dict:
    """A valid call: every required argument, the table's lines for the tool, and dry_run where it takes one."""
    names = list(tool["required"]) + [n for (t, n) in BY_TOOL if t == tool["name"]]
    call = {n: example(tool, n, ids) for n in dict.fromkeys(names) if n in tool["props"]}
    if "dry_run" in tool["props"]:
        call["dry_run"] = True
    return call


def one_of_each(tool: dict, ids: dict) -> list:
    """The valid call with each argument it does not hold added in turn (every value of an enum, each in its own call),
    as (what was added, arguments). A tool with no dry_run is a read, and so is each of these calls but reconcile's
    apply, page's and export's, which write where the test's record is."""
    base = arguments(tool, ids)
    out = []
    for name, prop in tool["props"].items():
        values = prop.get("enum") or ([example(tool, name, ids)] if name not in base else [])
        for v in values:
            out.append((f"{name}={v!r}", {**base, name: v}))
    return out


# what rb's command line says when it cannot parse a line (argparse's words, as rb gives them, with E_USAGE), and
# what rb mcp says when a call holds an argument its schema does not list
PARSE = re.compile(r"unrecognized arguments|invalid choice|expected one argument|expected at least one argument|"
                   r"the following arguments are required|invalid \w+ value|ignored explicit argument|unknown arguments? for ")


def _short(message: str) -> str:
    """argparse's message without the list of every command it could have been."""
    return (message or "").split(" (choose from ")[0]


def refusal(result: dict) -> str:
    """'' where the call did its job; else what rb said."""
    env = result.get("structuredContent") or {}
    if not result.get("isError") and env.get("ok"):
        return ""
    return "; ".join(f"{e.get('code')}: {_short(e.get('message'))}" for e in env.get("errors") or []) or str(result)[:400]


def not_parsed(result: dict) -> str:
    """'' unless rb could not parse the command line the call stands for; else what it said."""
    env = result.get("structuredContent") or {}
    said = [e for e in env.get("errors") or [] if e.get("code") == "E_USAGE" and PARSE.search(e.get("message") or "")]
    return "; ".join(_short(e["message"]) for e in said)


def _subparser(parser: Any, words: list) -> Any:
    for w in words:
        if parser._subparsers is None:
            return None
        [action] = [a for a in parser._subparsers._group_actions]
        if w not in action.choices:
            return None
        parser = action.choices[w]
    return parser


def against_the_parser(tool: dict, parser: Any) -> list:
    """Where the tool's schema and rb's declared command line part: each a sentence naming the tool."""
    name = tool["name"]
    sub = _subparser(parser, tool["words"])
    if sub is None:
        return [f"{name}: its words {tool['words']} name no rb command"]
    flags = {s: a for a in sub._actions for s in a.option_strings}
    said = []
    for key, flag in tool["opts"].items():
        prop = tool["props"].get(key)
        if prop is None:
            said.append(f"{name}: option {key} has no property in its schema")
            continue
        if key == "direction":
            for v in prop.get("enum") or []:
                if f"--{v}" not in flags or flags[f"--{v}"].nargs != 0:
                    said.append(f"{name}: direction {v!r} is no switch of rb {' '.join(tool['words'])}")
            continue
        action = flags.get(flag)
        if action is None:
            said.append(f"{name}: {key} becomes {flag}, which rb {' '.join(tool['words'])} does not take")
            continue
        boolean = prop.get("type") == "boolean"
        if boolean != (action.nargs == 0):
            said.append(f"{name}: {key} is {'a switch' if boolean else 'a value'} in its schema, {flag} is not")
        if prop.get("type") in ("array", "object") and key != "keys" and type(action).__name__ not in ("_AppendAction", "_ExtendAction") \
                and action.nargs not in ("+", "*"):
            said.append(f"{name}: {key} is a list, and {flag} keeps only its last value")
        if prop.get("enum") and action.choices is not None and set(prop["enum"]) != set(action.choices):
            said.append(f"{name}: {key} lists {prop['enum']}, {flag} takes {list(action.choices)}")
    positional = [a for a in sub._actions if not a.option_strings and type(a).__name__ != "_SubParsersAction"]
    args = [a.lstrip("*") for a in tool["args"]] + MCP_OWN_POSITIONAL.get(name, [])
    if [a.dest for a in positional] != args:
        said.append(f"{name}: its positionals {args} are rb {' '.join(tool['words'])}'s {[a.dest for a in positional]}")
    for a, action in zip(tool["args"], positional):
        if a.startswith("*") != (action.nargs in ("*", "+")):
            said.append(f"{name}: {a.lstrip('*')} is {'a list' if a.startswith('*') else 'one value'} in its schema, not on the command line")
    for a in sub._actions:
        if type(a).__name__ == "_SubParsersAction":
            continue
        required = a.required if a.option_strings else a.nargs not in ("?", "*")
        key = a.dest if not a.option_strings else next((k for k, f in tool["opts"].items() if f in a.option_strings), None)
        if required and a.dest != "help" and key not in tool["required"]:
            said.append(f"{name}: rb {' '.join(tool['words'])} requires {a.option_strings[0] if a.option_strings else a.dest}, its schema does not")
    for key in tool["props"]:
        if key not in tool["opts"] and key not in args and name not in MCP_OWN.get(key, ()):
            said.append(f"{name}: {key} is in its schema and becomes nothing on the command line")
    return said


def schemas_listed(listed: list, assets: dict) -> list:
    """Where rb mcp's tools/list and assets/mcp.json part."""
    said = []
    served = {t["name"]: t for t in listed}
    for t in assets["tools"]:
        s = served.pop(t["name"], None)
        if s is None:
            said.append(f"{t['name']}: in assets/mcp.json, not listed by rb mcp")
        elif (s["description"], s["inputSchema"]["properties"], s["inputSchema"]["required"]) != (t["description"], t["props"], t["required"]):
            said.append(f"{t['name']}: rb mcp lists another schema than assets/mcp.json")
    said += [f"{n}: listed by rb mcp, not in assets/mcp.json" for n in served]
    return said

