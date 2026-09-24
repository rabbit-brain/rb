"""`rb mcp`: the research state over the Model Context Protocol (stdio, JSON-RPC 2.0, one message per line).

Each tool is the CLI command of the same name, run in-process with `--json`: the same rules, the same envelope, the same
error codes. What differs is who is recorded. Every call on a connection is recorded as `agent:<client name>` (from the
client's `initialize`, or `--agent`), whatever RB_ACTOR says, with `via: mcp`: a model is making the call. So freezing,
deciding, amending and retracting what something rests on come back as `E_HUMAN_ONLY` with `handoff.command`, the line
for the person to run in their own terminal. No dependency beyond the standard library.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from typing import Any, Callable, Optional, TextIO

from . import __version__
from .ledger import session

PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = (
    "Rabbit Brain keeps this project's research state. Read the `context` tool (or the rb://context resource) before anything "
    "else: it is the state of the work and replaces any summary. You propose (questions, hypotheses, experiments, settings with "
    "sources, claims, evidence); rb checks sources and computes every verdict; the person decides. Never compute a verdict "
    "yourself. Freezing, deciding, amending and retracting what something rests on are the person's calls: those tools return "
    "E_HUMAN_ONLY with handoff.command; give that command to the person as it is. Report a claim's standing exactly as rb "
    "prints it, with every blocking reason."
)

S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
LIST = {"type": "array", "items": {"type": "string"}}
NUMS = {"type": "object", "additionalProperties": {"type": "number"}}
TEXTS = {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean"]}}
WHY = {"type": "string", "description": "the reason, recorded with the call"}
AMEND = {"type": "boolean", "description": "the experiment is frozen: change it anyway. A person's call: returns the command for the person, with why"}


def _d(schema: dict, text: str) -> dict:
    return {**schema, "description": text}


# name, description, positional arguments (in order), options (property -> flag), properties, required
TOOLS: list[dict] = [
    {"name": "context", "description": "The whole research state as the Markdown handoff pack. Read it first.", "args": [], "props": {}},
    {"name": "status", "description": "Claims with their standing, what needs a person, what an agent can do, every object (JSON).",
     "args": ["experiment"], "props": {"experiment": _d(S, "only this experiment")}},
    {"name": "show", "description": "One object in full: a claim with its verdict and observations, an experiment with its spec, evidence with its receipt, a setting (int8/optim.lr) with its history.",
     "args": ["id"], "props": {"id": S}, "required": ["id"]},
    {"name": "compare", "description": "An experiment's table: variants by metrics, with the change against the baseline.",
     "args": ["experiment"], "props": {"experiment": S}, "required": ["experiment"]},
    {"name": "log", "description": "Recent writes: who, through what, what changed.", "args": ["id"], "opts": {"n": "-n"},
     "props": {"id": _d(S, "only this object, or <experiment>/<setting>"), "n": _d({"type": "integer"}, "how many (default 20)")}},
    {"name": "question_add", "description": "Add a question the research is trying to answer.", "args": ["text"], "opts": {"id": "--id"},
     "props": {"text": S, "id": S}, "required": ["text"]},
    {"name": "hypothesis_add", "description": "Add what you expect, and why.", "args": ["statement"],
     "opts": {"why": "--why", "question": "--question", "id": "--id"}, "props": {"statement": S, "why": S, "question": _d(S, "the question id it answers"), "id": S},
     "required": ["statement"]},
    {"name": "assumption_add", "description": "Add something the work takes for granted.", "args": ["text"],
     "opts": {"experiment": "--experiment", "id": "--id"}, "props": {"text": S, "experiment": _d(LIST, "experiments it applies to"), "id": S}, "required": ["text"]},
    {"name": "experiment_add", "description": "Add an experiment: what would test a hypothesis, with its variants.", "args": ["title"],
     "opts": {"id": "--id", "hypothesis": "--hypothesis", "baseline": "--baseline", "candidate": "--candidate", "varies": "--varies", "like": "--like", "note": "--note"},
     "props": {"title": S, "id": S, "hypothesis": _d(LIST, "hypothesis ids it tests"), "baseline": _d(S, "the baseline variant's name"),
               "candidate": _d(LIST, "candidate variant names"), "varies": _d(LIST, "settings the variants differ in on purpose; any other difference is a confound"),
               "like": _d(S, "copy another experiment's shared settings (as provisional) and what it varies"), "note": S}, "required": ["title"]},
    {"name": "variant_add", "description": "Add a variant to an experiment.", "args": ["experiment", "name"],
     "opts": {"role": "--role", "note": "--note", "amend": "--amend", "why": "--why"},
     "props": {"experiment": S, "name": S, "role": {"type": "string", "enum": ["baseline", "candidate", "control", "ablation"]}, "note": S, "amend": AMEND, "why": WHY},
     "required": ["experiment", "name", "role"]},
    {"name": "metric_add", "description": "Add a metric to the catalogue: its unit, which way is better, other names evidence may use.", "args": ["name"],
     "opts": {"unit": "--unit", "direction": None, "alias": "--alias", "description": "--description"},
     "props": {"name": S, "unit": S, "direction": {"type": "string", "enum": ["minimize", "maximize"]}, "alias": LIST, "description": S}, "required": ["name"]},
    {"name": "spec_set", "description": ("Record a setting and where its value comes from; rb checks the source now when it can. Sources: file#key.path "
                                         "(YAML/JSON/TOML; the value is read when not given), file:LINE, a file with quote, run:PATH#/pointer, https://..., note:text. "
                                         "Or from_file with keys to record many settings from a config."),
     "args": ["experiment", "name", "value"],
     "opts": {"source": "--source", "quote": "--quote", "term": "--term", "commit": "--commit", "locator": "--locator", "variant": "--variant",
              "unknown": "--unknown", "per_run": "--per-run", "optional": "--optional", "cited": "--cited", "cited_source": "--cited-source",
              "from_file": "--from", "keys": "--keys", "note": "--note", "no_verify": "--no-verify", "amend": "--amend", "why": "--why"},
     "props": {"experiment": S, "name": _d(S, "the setting; <variant>.<name> for one variant's own value"),
               "value": _d({"type": ["string", "number", "boolean", "array"]}, "the value; optional with file#key or run: sources"),
               "source": S, "quote": S, "term": S, "commit": S, "locator": S, "variant": S, "unknown": B, "per_run": B, "optional": B,
               "cited": _d({"type": ["string", "number", "boolean"]}, "the value a cited source used"), "cited_source": S,
               "from_file": _d(S, "a YAML/JSON/TOML config to record settings from, with keys"), "keys": _d(LIST, "glob patterns of the keys that define the experiment"),
               "note": S, "no_verify": B, "amend": AMEND, "why": WHY},
     "required": ["experiment"]},
    {"name": "spec_verify", "description": "Re-read settings' sources; verified only if the source states the value.", "args": ["experiment", "*names"],
     "props": {"experiment": S, "names": _d(LIST, "settings (default: every setting with a source)")}, "required": ["experiment"]},
    {"name": "spec_vary", "description": "Declare settings the variants differ in on purpose.", "args": ["experiment", "*names"],
     "opts": {"amend": "--amend", "why": "--why"}, "props": {"experiment": S, "names": LIST, "amend": AMEND, "why": WHY}, "required": ["experiment", "names"]},
    {"name": "claim_add", "description": ("Add a claim with exactly one criterion (at_most, at_least, or equals with tolerance). Write it before the run "
                                          "that tests it: evidence attached earlier never counts. metric: epe, int8.epe, candidate.epe, or change.epe."),
     "args": ["statement"],
     "opts": {"experiment": "--experiment", "metric": "--metric", "at_most": "--at-most", "at_least": "--at-least", "equals": "--equals",
              "tolerance": "--tolerance", "over": "--over", "min_n": "--min-n", "noise": "--noise", "hypothesis": "--hypothesis",
              "source": "--source", "quote": "--quote", "locator": "--locator", "note": "--note", "id": "--id", "amend": "--amend", "why": "--why"},
     "props": {"statement": S, "experiment": S, "metric": S, "at_most": N, "at_least": N, "equals": N, "tolerance": N,
               "over": {"type": "string", "enum": ["each", "mean"]}, "min_n": {"type": "integer"}, "noise": N, "hypothesis": S,
               "source": _d(S, "where a cited number comes from"), "quote": S, "locator": S, "note": S, "id": S, "amend": AMEND, "why": WHY},
     "required": ["statement", "experiment", "metric"]},
    {"name": "evidence_attach", "description": ("Attach numbers to an experiment, with a receipt. metrics: {\"int8.epe\": 5.64}; or from_file; or run "
                                                "(an rb review run). set: per-run settings such as {\"seed\": 2}. config: the run's resolved config, checked against the spec."),
     "args": ["experiment"],
     "opts": {"variant": "--variant", "from_file": "--from", "run": "--run", "config": "--config", "set": "--set", "commit": "--commit",
              "artifact": "--artifact", "link": "--link", "command": "--command", "again": "--again", "why": "--why", "note": "--note"},
     "props": {"experiment": S, "metrics": NUMS, "variant": S, "from_file": S, "run": S, "config": S, "set": TEXTS, "commit": S,
               "artifact": LIST, "link": {"type": "object", "additionalProperties": {"type": "string"}}, "command": _d(S, "what produced the numbers"),
               "again": _d(B, "attach identical evidence again on purpose, with why"), "why": WHY, "note": S},
     "required": ["experiment"]},
    {"name": "retract", "description": "Take something back: it stays on record and stops counting (a person's call once something rests on it).",
     "args": ["subject"], "opts": {"why": "--why"}, "props": {"subject": S, "why": WHY}, "required": ["subject", "why"]},
    {"name": "freeze", "description": "A person's call: returns the command for the person to lock an experiment before the runs that count.",
     "args": ["experiment"], "opts": {"why": "--why"}, "props": {"experiment": S, "why": WHY}, "required": ["experiment", "why"]},
    {"name": "decide", "description": "A person's call: returns the command for the person to accept, reject or investigate a claim, hypothesis, setting, ...",
     "args": ["subject", "outcome"], "opts": {"why": "--why"},
     "props": {"subject": S, "outcome": {"type": "string", "enum": ["accept", "reject", "investigate"]}, "why": WHY}, "required": ["subject", "outcome", "why"]},
]
RESOURCES = [
    {"uri": "rb://context", "name": "context", "description": "The research state as the Markdown handoff pack", "mimeType": "text/markdown"},
    {"uri": "rb://status", "name": "status", "description": "The research state as JSON: claims, open items, every object", "mimeType": "application/json"},
    {"uri": "rb://docs", "name": "AGENTS.md", "description": "The manual: rules, commands, what verified and established mean", "mimeType": "text/markdown"},
]
_BY_NAME = {t["name"]: t for t in TOOLS}


def _cli_words(name: str) -> list[str]:
    return name.split("_", 1) if name.endswith(("_add", "_set", "_verify", "_vary", "_attach")) else [name]


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return f"{v!r}" if isinstance(v, float) else str(v)


def argv_for(name: str, arguments: dict) -> list[str]:
    """The `rb` command line a tool call stands for."""
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(name)
    arguments = dict(arguments or {})
    unknown = set(arguments) - set(tool["props"]) - ({"metrics"} if name == "evidence_attach" else set())
    if unknown:
        raise ValueError(f"unknown argument(s) for {name}: {', '.join(sorted(unknown))}")
    argv = _cli_words(name)
    positional: list[str] = []
    for a in tool["args"]:
        if a.startswith("*"):
            vals = arguments.get(a[1:]) or []
            positional += [str(x) for x in (vals if isinstance(vals, list) else [vals])]
        elif arguments.get(a) is not None:
            positional.append(_scalar(arguments[a]))
    if name == "evidence_attach":
        positional += [f"{k}={_scalar(v)}" for k, v in (arguments.get("metrics") or {}).items()]
    for key, flag in (tool.get("opts") or {}).items():
        v = arguments.get(key)
        switch = tool["props"].get(key, {}).get("type") == "boolean"
        if v is None or (v is False and switch):     # a switch left off; a value of false is a value (cited=false)
            continue
        if key == "direction":
            argv.append(f"--{v}")
        elif v is True and switch:
            argv.append(flag)
        elif isinstance(v, dict):
            for k, x in v.items():
                argv += [flag, f"{k}={_scalar(x)}"]
        elif isinstance(v, list) and key not in ("keys",):
            for x in v:
                argv += [flag, _scalar(x)]
        elif isinstance(v, list):
            argv += [flag, ",".join(str(x) for x in v)]
        else:
            argv += [flag, _scalar(v)]
    if any(x.startswith("-") for x in positional):        # after --, a value may start with '-' (-O2, -10%)
        return argv + ["--", *positional]
    return argv[:len(_cli_words(name))] + positional + argv[len(_cli_words(name)):]


def run_tool(name: str, arguments: dict, agent: str) -> dict:
    """Run one tool as the CLI command it stands for; returns the envelope."""
    from .cli import main
    argv = argv_for(name, arguments)
    buf, err = io.StringIO(), io.StringIO()
    cut = argv.index("--") if "--" in argv else len(argv)
    with session("mcp", agent), contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = main([*argv[:cut], "--json", *argv[cut:]])
    try:
        env = json.loads(buf.getvalue())
    except ValueError:
        env = {"ok": False, "errors": [{"code": "E_INTERNAL", "message": (err.getvalue() or buf.getvalue())[-2000:]}]}
    env["exit_code"] = code
    return env


def _tool_result(name: str, env: dict) -> dict:
    if name == "context" and env.get("ok"):
        text = (env.get("data") or {}).get("markdown", "")
        env = {k: v for k, v in env.items() if k != "data"} | {"data": {"markdown": text}}
    else:
        text = json.dumps(env, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}], "structuredContent": env, "isError": not env.get("ok", False)}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@+-]+", "-", text.strip().lower()).strip("-")[:60] or "mcp-client"


class Server:
    def __init__(self, agent: Optional[str] = None) -> None:
        self.agent = agent
        self.initialized = False

    def handle(self, msg: Any) -> Optional[dict]:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            mid = msg.get("id") if isinstance(msg, dict) else None
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "invalid request: a JSON-RPC 2.0 object with a method"}}
        method, mid = msg.get("method"), msg.get("id")
        if "id" not in msg:                # a notification: nothing to answer
            return None
        try:
            result = self._dispatch(method, msg.get("params") or {})
        except _RPCError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
        except Exception as e:  # noqa: BLE001 - the server must answer every request
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _dispatch(self, method: Optional[str], params: dict) -> Any:
        if method == "initialize":
            client = (params.get("clientInfo") or {}).get("name") or ""
            if not self.agent:
                self.agent = _slug(client) if client else "mcp-client"
            asked = params.get("protocolVersion")
            self.initialized = True
            return {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                    "serverInfo": {"name": "rabbit-brain", "version": __version__}, "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t["name"], "description": t["description"],
                               "inputSchema": {"type": "object", "properties": t["props"], "required": t.get("required", []), "additionalProperties": False}}
                              for t in TOOLS]}
        if method == "tools/call":
            name = params.get("name")
            if name not in _BY_NAME:
                raise _RPCError(-32602, f"unknown tool {name!r}")
            try:
                env = run_tool(name, params.get("arguments") or {}, self.agent or "mcp-client")
            except ValueError as e:
                env = {"ok": False, "errors": [{"code": "E_USAGE", "message": str(e)}]}
            return _tool_result(name, env)
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/read":
            uri = params.get("uri")
            if uri == "rb://docs":
                from .cli import docs_text
                return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": docs_text()}]}
            if uri in ("rb://context", "rb://status"):
                env = run_tool(uri[5:], {}, self.agent or "mcp-client")
                text = (env.get("data") or {}).get("markdown", "") if uri == "rb://context" and env.get("ok") else json.dumps(env, indent=1, default=str)
                return {"contents": [{"uri": uri, "mimeType": "text/markdown" if uri == "rb://context" else "application/json", "text": text}]}
            raise _RPCError(-32002, f"no resource {uri!r}")
        raise _RPCError(-32601, f"method not found: {method}")


class _RPCError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, agent: Optional[str] = None,
          log: Callable[[str], None] = lambda s: print(s, file=sys.stderr)) -> int:
    server = Server(agent)
    log(f"rb mcp {__version__}: serving the research state over stdio; every call is recorded as an agent's")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        if isinstance(msg, list) and not msg:
            replies = [{"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request: an empty batch"}}]
            msg = {}
        else:
            replies = [r for r in (server.handle(m) for m in (msg if isinstance(msg, list) else [msg])) if r is not None]
        if replies:
            stdout.write(json.dumps(replies if isinstance(msg, list) else replies[0], default=str) + "\n")
            stdout.flush()
    return 0
