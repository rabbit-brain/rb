"""The research-state commands. The rules live in ledger.py; this file parses arguments and says what happened.

    rb init "<title>"                      start .rb/ here
    rb question|hypothesis|assumption add  what the work asks, expects, takes for granted
    rb experiment add | rb variant add     what would test it, and the arms it compares
    rb metric add                          the catalogue: a metric's unit, direction and other names
    rb spec set | rb spec verify           settings with sources; rb checks them
    rb claim add                           a criterion evidence can meet or miss
    rb evidence attach                     numbers, with a receipt
    rb freeze | rb decide | rb retract     a person's calls (retract: anyone, while nothing rests on it)
    rb status | rb show | rb compare | rb log | rb context   reading it
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import io
import json
import math
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Optional

from .errors import EXIT_CHECK_FAILED, EXIT_OK, RBError
from .investigation import Cited, Experiment, Source
from .ledger import SETTLED, Ledger, evidence_from_run, parse_address, show
from .runs import resolve_run, runs_dir
from .actor import runtime_label
from .sources import Unresolved, as_value, flatten, parse_structured, structured_kind

GATES = ["unestablished", "untested", "refuted", "not_reproduced", "undecided", "unknown", "stale"]
LEDGER_COMMANDS = ["init", "question add", "hypothesis add", "assumption add", "experiment add", "variant add", "metric add",
                   "spec set", "spec verify", "spec vary", "claim add", "evidence attach", "approve", "freeze", "decide", "retract",
                   "status", "show", "compare", "log", "context"]
STATUS_CAVEATS = 3
RESEARCH_DOCTOR_HELP = "check the research state: who you are recorded as, edits made outside rb, merge leftovers, sources outside the repository"


# ---------------------------------------------------------------- parsing helpers


def parse_value(text: str) -> Any:
    """A setting's value from the command line: JSON when it parses as a number, boolean, quoted string or list of those
    (so 1e-4 is a number and "6" in quotes is text), otherwise the text itself."""
    if not text.strip():
        raise RBError("E_OBJECT_INVALID", message="An empty value is not a value; record a setting nobody has found with --unknown.")
    try:
        v = json.loads(text)
    except ValueError:
        return text

    def scalar(x: Any) -> bool:
        return isinstance(x, (bool, int)) or (isinstance(x, str) and bool(x.strip())) or (isinstance(x, float) and math.isfinite(x))
    if scalar(v) or (isinstance(v, list) and v and all(scalar(x) for x in v)):
        return v
    if v is None:
        raise RBError("E_OBJECT_INVALID", message="null is not a value; record a setting nobody has found with --unknown.")
    raise RBError("E_OBJECT_INVALID", message=f"A value is a finite number, true/false, non-empty text, or a non-empty list of those; got {text!r}.")


def root_relative(path: str, root: Path) -> str:
    """A path as typed (relative to where you are) becomes relative to the directory holding .rb/, keeping the last
    component as named: a symlink stays the symlink, so repointing it later shows up as a change."""
    p = Path(path)
    absolute = p if p.is_absolute() else Path.cwd() / p
    full = Path(os.path.realpath(absolute.parent)) / absolute.name
    try:
        return full.relative_to(root).as_posix()
    except ValueError:
        return str(full)


def parse_source(spec: Optional[str], quote: Optional[str], commit: Optional[str], locator: Optional[str], term: Optional[str], root: Path) -> Optional[Source]:
    """`path#key` (YAML, JSON, TOML), `path:LINE`, `path` with --quote, `run:PATH#/pointer`, `https://...`, `note:text`."""
    if spec is None:
        if quote or commit or locator or term:
            raise RBError("E_OBJECT_INVALID", message="--quote, --commit, --locator and --term describe a --source; give the --source too.")
        return None
    fields: dict[str, Any] = {"locator": locator}
    if spec.startswith("run:"):
        path, sep, pointer = spec[4:].rpartition("#")
        if not sep or not path:
            raise RBError("E_OBJECT_INVALID", message=f"A run source is run:PATH#/json/pointer, e.g. run:rb-runs/<id>#/seeds/torch (got {spec!r}).")
        fields.update(kind="run", path=root_relative(path, root), pointer=pointer)
    elif spec.startswith(("http://", "https://")):
        fields.update(kind="url", url=spec, quote=quote)
    elif spec.startswith("note:"):
        fields.update(kind="note", note=spec[5:].strip())
    else:
        path, key, line = spec, None, None
        head, sep, tail = spec.partition("#")
        if sep and tail and structured_kind(head):
            path, key = head, tail
        else:
            h2, sep2, t2 = spec.rpartition(":")
            if sep2 and t2.isdigit() and h2:
                path, line = h2, int(t2)
        fields.update(kind="file", path=root_relative(path, root), key=key, line=line, quote=quote, commit=commit, term=term)
    if fields["kind"] != "file" and (commit or term):
        raise RBError("E_OBJECT_INVALID", message="--commit and --term apply to a file source.")
    if fields["kind"] in ("run", "note") and quote:
        raise RBError("E_OBJECT_INVALID", message=f"--quote applies to a file or url source; a {fields['kind']} source has no text to quote.")
    try:
        return Source(**{k: v for k, v in fields.items() if v is not None})
    except ValueError as exc:
        msg = exc.errors()[0]["msg"] if hasattr(exc, "errors") else str(exc)
        raise RBError("E_OBJECT_INVALID", message=f"--source {spec}: {msg}")


def parse_pairs(items: Optional[list[str]], what: str, numeric: bool) -> dict:
    out: dict[str, Any] = {}
    for item in items or []:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise RBError("E_OBJECT_INVALID", message=f"{what} is name=value (got {item!r}).")
        if key in out:
            raise RBError("E_OBJECT_INVALID", message=f"{what} {key} is given twice ({out[key]} and {val}); give each name once.")
        if numeric:
            try:
                x = float(val)
            except ValueError:
                raise RBError("E_OBJECT_INVALID", message=f"{what} {key}: {val!r} is not a number.")
            if not math.isfinite(x):
                raise RBError("E_OBJECT_INVALID", message=f"{what} {key} must be finite.")
            out[key] = x
        else:
            out[key] = parse_value(val) if what == "--set" else val
    return out


def metrics_from_file(path: Path) -> tuple[dict[str, float], list[str]]:
    """Numbers in a JSON (or YAML, TOML) file, nested keys joined with dots. Everything that is not a number, an empty
    object included, is named rather than dropped; two keys that join to the same name are refused."""
    kind = structured_kind(str(path)) or "json"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {path}")
    except OSError as exc:
        raise RBError("E_FILE_NOT_FOUND", message=f"{path} could not be read: {exc}")
    try:
        doc = parse_structured(text, kind, str(path))
    except Unresolved as u:
        raise RBError("E_OBJECT_INVALID", message=u.reason)
    if not isinstance(doc, dict):
        raise RBError("E_OBJECT_INVALID", message=f"{path} must hold an object of metric names to numbers.")
    numbers: dict[str, float] = {}
    skipped: list[str] = []

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict) and node:
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(node, (int, float)) and not isinstance(node, bool) and math.isfinite(node):
            if prefix in numbers:
                raise RBError("E_OBJECT_INVALID", message=f"{path}: two keys both read as {prefix!r} once nested keys are joined with dots.")
            numbers[prefix] = float(node)
        else:
            skipped.append(prefix)
    walk("", doc)
    return numbers, skipped


def split_pairs(tokens: Optional[list[str]]) -> list[str]:
    return [t for t in tokens or [] if "=" in t]


def _actor_line(out: Any, led: Ledger) -> None:
    a = led.actor()
    stamp = f" (RB_ACTOR={a.ignored} is ignored inside {runtime_label(a.runtime)})" if a.ignored and a.runtime else ""
    out.say(f"· recorded as {a.id}{stamp}")
    out.data["actor"] = {"id": a.id, "via": a.via, **({"ignored_rb_actor": a.ignored} if a.ignored else {})}


def _obj(kind: str, obj: Any) -> dict:
    return {"kind": kind, **obj.model_dump(mode="json")}


# ---------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace, out: Any) -> int:
    title = args.title or Path.cwd().name
    led = Ledger.init(Path.cwd(), title, id=args.id or None)
    inv = led.investigation
    out.say(f"Investigation {inv.id!r} started: {led.dir}", "Plain files: commit .rb/ with the code, so the state travels with it.")
    _actor_line(out, led)
    out.data.update({"object": _obj("investigation", inv), "dir": str(led.dir)})
    out.next = ['rb experiment add "<what you are testing>" --baseline <name> --candidate <name> --varies <setting>',
                'rb question add "<what are you trying to establish?>"']
    return EXIT_OK


def cmd_question_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    q = led.add_question(args.text, id=args.id)
    out.say(f"{q.id}: {q.text}")
    _actor_line(out, led)
    out.data["object"] = _obj("question", q)
    out.next = [f'rb hypothesis add "<what you expect>" --question {q.id} --why "..."']
    return EXIT_OK


def cmd_hypothesis_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    h = led.add_hypothesis(args.statement, why=args.why or "", question=args.question, id=args.id)
    out.say(f"{h.id} ({h.status}): {h.statement}")
    _actor_line(out, led)
    out.data["object"] = _obj("hypothesis", h)
    out.next = [f'rb experiment add "<what would test it>" --hypothesis {h.id} --baseline <name> --candidate <name>']
    return EXIT_OK


def cmd_assumption_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    a = led.add_assumption(args.text, applies_to=args.experiment or [], id=args.id)
    out.say(f"{a.id}: {a.text}")
    _actor_line(out, led)
    out.data["object"] = _obj("assumption", a)
    return EXIT_OK


def cmd_experiment_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    varies = [v.strip() for item in args.varies or [] for v in item.split(",") if v.strip()]
    e = led.add_experiment(args.title, id=args.id, hypotheses=args.hypothesis or [], baseline=args.baseline, candidates=args.candidate or [],
                           varies=varies, note=args.note, like=args.like)
    arms = " vs ".join(f"{v.name} ({v.role})" for v in e.variants)
    out.say(f"Experiment {e.id}: {e.title}" + (f" · {arms}" if arms else "") + (f" · varies {', '.join(e.varies)}" if e.varies else ""))
    if args.like:
        out.say(f"  settings copied from {args.like} as provisional: {len(e.settings)}; verify them with rb spec verify {e.id}")
    if not e.variants:
        out.say(f"  No variants yet. A comparison needs them: rb variant add {e.id} <name> --role baseline|candidate")
    _actor_line(out, led)
    out.data["object"] = _obj("experiment", e)
    out.next = [f"rb spec set {e.id} --from <config.yaml> --keys \"<the keys that define it>\"",
                f'rb claim add "<what it should show>" -e {e.id} --metric <name> --at-most <x>']
    return EXIT_OK


def cmd_variant_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    v = led.add_variant(args.experiment, args.name, args.role, note=args.note, amend=_amend(args))
    out.say(f"{args.experiment}: variant {v.name} ({v.role})")
    _actor_line(out, led)
    out.data.update({"object": {"kind": "variant", "id": f"{args.experiment}/{v.name}", "experiment_id": args.experiment, **v.model_dump(mode="json")}})
    out.next = [f"rb spec set {args.experiment} {v.name}.<setting> <value> --source <file#key>"]
    return EXIT_OK


def cmd_metric_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    direction = "minimize" if args.minimize else ("maximize" if args.maximize else "none")
    m = led.add_metric(args.name, unit=args.unit or "", direction=direction, aliases=args.alias or [], description=args.description or "")
    word = {"minimize": "lower is better", "maximize": "higher is better", "none": "no direction"}[direction]
    out.say(f"Metric {m.id}" + (f" ({m.unit})" if m.unit else "") + f": {word}" + (f" · also called {', '.join(m.aliases)}" if m.aliases else ""))
    _actor_line(out, led)
    out.data["object"] = _obj("metric", m)
    return EXIT_OK


def _amend(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "amend", False):
        if not getattr(args, "why", None):
            raise RBError("E_OBJECT_INVALID", message="--amend needs --why \"<reason>\": the reason is what the amendment records.")
        return args.why
    return None


def cmd_spec_set(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    if args.from_file:
        return _spec_from(args, out, led)
    if not args.name:
        raise RBError("E_USAGE", message="rb spec set <experiment> <name> [<value>] --source <file#key>, or rb spec set <experiment> --from <file> --keys \"...\"")
    if args.value is not None and args.unknown:
        raise RBError("E_OBJECT_INVALID", message="Give a value or --unknown, not both.")
    value = parse_value(args.value) if args.value is not None else None
    source = parse_source(args.source, args.quote, args.commit, args.locator, args.term, led.root)
    cited = None
    if args.cited is not None:
        cited = Cited(value=parse_value(args.cited), source=parse_source(args.cited_source, None, None, None, None, led.root) if args.cited_source else None)
    required = False if args.optional else (True if args.required else None)
    name = f"{args.variant}.{args.name}" if args.variant else args.name
    if value is None and not args.unknown and not args.per_run and source is not None and (
            source.key or source.kind == "run" or (source.line and structured_kind(str(source.path)) == "yaml")):
        value = _read_value(led, source, name)
    setting, row = led.set_setting(args.experiment, name, value, unknown=args.unknown, source=source, required=required,
                                   per_run=True if args.per_run else None, cited=cited, note=args.note, amend=_amend(args), verify=not args.no_verify)
    status = setting.status + ("" if setting.required else " (optional)")
    where = f" in {setting.source.label()}" if setting.status == "verified" and setting.source else (f" · source {setting.source.label()}" if setting.source else "")
    if setting.per_run:
        out.say(f"{args.experiment}/{name} · per-run: each piece of evidence gives its own, with --set {name.split('.')[-1]}=<value> on attach")
    else:
        out.say(f"{args.experiment}/{name} = {show(setting.value)} · {status}{where}")
    if row is not None and not row["ok"]:
        _say_row(out, row)
    elif setting.status == "provisional" and (setting.source is None or not setting.source.checkable()):
        out.say("  It stays provisional until a file (file#key, file:LINE, or --quote) or a run's JSON that states it is its source; a url or a note is recorded but cannot be checked.")
    _actor_line(out, led)
    failures = [_failure(row)] if row is not None and not row["ok"] and not row.get("skipped") else []
    out.data.update({"object": {"kind": "setting", "id": f"{args.experiment}/{name}", "experiment_id": args.experiment, "address": f"{args.experiment}/{name}", **setting.model_dump(mode="json")},
                     "outcome": {"passed": not failures, "failures": failures}})
    if setting.status == "provisional" and setting.source is not None and setting.source.checkable() and args.no_verify:
        out.next = [f"rb spec verify {args.experiment} {name}"]
    return EXIT_CHECK_FAILED if failures else EXIT_OK


def _read_value(led: Ledger, source: Source, name: str) -> Any:
    """With a key path or a run pointer the value is optional: rb reads it."""
    from .sources import get_key, key_names, pointer_get, read_file, run_json_path, yaml_key_at
    try:
        if source.kind == "run":
            doc = json.loads(run_json_path(led.root, str(source.path)).read_text(encoding="utf-8"))
            return pointer_get(doc, str(source.pointer))
        data, _ = read_file(led.root, str(source.path), source.commit)
        text = data.decode("utf-8", errors="replace")
        key = source.key
        if key is None and source.line:
            key = yaml_key_at(text, source.line)
            if key is None:
                raise RBError("E_SOURCE_UNRESOLVED", message=f"{source.label()} holds no single value to read; give the value.")
            if not key_names(key, name, source.term):
                raise RBError("E_SOURCE_UNRESOLVED", message=f"{source.label()} is the key {key}, not {name}. Point at {name}'s own line or key, or give --term.")
        doc = parse_structured(text, structured_kind(str(source.path)) or "json", str(source.path))
        got = as_value(get_key(doc, str(key)))
    except Unresolved as u:
        raise RBError(u.code, message=u.reason)
    except (KeyError, OSError, ValueError) as exc:
        raise RBError("E_SOURCE_UNRESOLVED", message=f"{source.label()} has no value to read ({type(exc).__name__}: {exc}); give the value.")
    if isinstance(got, (dict, list)) and not (isinstance(got, list) and got and all(not isinstance(x, (dict, list)) for x in got)):
        raise RBError("E_OBJECT_INVALID", message=f"{source.label()} is a section, not a value.")
    return got


def _not_a_value(v: Any) -> Optional[str]:
    """Why a config value is not one rb records as a setting, or None."""
    if v is None:
        return "empty"
    if isinstance(v, dict):
        return "a section"
    if isinstance(v, list):
        return None if v and all(_not_a_value(x) is None and not isinstance(x, list) for x in v) else "a list of sections or an empty list"
    if isinstance(v, bool) or isinstance(v, int):
        return None
    if isinstance(v, float):
        return None if v == v and v not in (float("inf"), float("-inf")) else "not a finite number"
    if isinstance(v, str):
        return None if v.strip() else "empty text"
    return f"a {type(v).__name__}"


def _spec_from(args: argparse.Namespace, out: Any, led: Ledger) -> int:
    """Record the keys that define an experiment from its config, each sourced by key path and verified."""
    path = args.from_file
    kind = structured_kind(path)
    if kind is None:
        raise RBError("E_OBJECT_INVALID", message=f"--from reads YAML, JSON or TOML; {path} is none of those.")
    rel = root_relative(path, led.root)
    try:
        text = (led.root / rel if not Path(rel).is_absolute() else Path(rel)).read_text(encoding="utf-8")
    except OSError as exc:
        raise RBError("E_FILE_NOT_FOUND", message=f"{path}: {exc}")
    try:
        from .sources import unwrap_wandb
        doc = unwrap_wandb(parse_structured(text, kind, path))
    except Unresolved as u:
        raise RBError("E_OBJECT_INVALID", message=u.reason)
    flat = {k: v for k, v in flatten(doc).items() if not str(k).startswith("_")}
    if not args.keys:
        tops = sorted({k.split(".")[0] for k in flat})
        sections = [t for t in tops if any(k.startswith(t + ".") for k in flat)]
        example = f"{sections[0]}.*" if sections else (tops[0] if tops else "")
        raise RBError("E_OBJECT_INVALID", message=f"{path} has {len(flat)} keys; pick the ones that define the experiment with --keys, e.g. --keys \"{example}\"" if tops else f"{path} has no keys.",
                      fix="Choosing which keys define the experiment is the researcher's call: a key like log_every_n_steps should not stop runs counting when it changes.",
                      problems=[f"top-level: {', '.join(tops[:20])}"])
    patterns = [p.strip() for item in args.keys for p in item.split(",") if p.strip()]
    chosen = [k for k in flat if any(fnmatch.fnmatchcase(k, p) for p in patterns)]
    if not chosen:
        raise RBError("E_OBJECT_INVALID", message=f"--keys {', '.join(patterns)} matches none of {path}'s {len(flat)} keys.")
    rows, skipped, todo = [], [], []
    for key in chosen:            # decide every key first, so a value rb cannot record never leaves half the keys written
        value = as_value(flat[key])
        why_not = _not_a_value(value)
        if why_not:
            skipped.append(f"{key} ({why_not})")
        else:
            todo.append((key, value))
    for key, value in todo:
        name = f"{args.variant}.{key}" if args.variant else key
        src = Source(kind="file", path=rel, key=key, commit=args.commit)
        setting, row = led.set_setting(args.experiment, name, value, source=src, required=False if args.optional else None,
                                       amend=_amend(args), verify=not args.no_verify)
        rows.append((name, setting, row))
    verified = sum(1 for _, s, _ in rows if s.status == "verified")
    commit = next((r["commit"] for _, _, r in rows if r and r.get("commit")), None)
    out.say(f"{args.experiment}: {len(rows)} settings from {rel}" + (f" @{commit[:7]}" if commit else "") + f" · {verified} verified")
    for name, s, row in rows:
        if row is not None and not row["ok"]:
            _say_row(out, row)
    if skipped:
        out.warn(f"not values, so not settings: {', '.join(skipped[:12])}")
    _actor_line(out, led)
    failures = [_failure(r) for _, _, r in rows if r is not None and not r["ok"] and not r.get("skipped")]
    out.data.update({"object": {"kind": "settings", "experiment_id": args.experiment, "from": rel,
                                "settings": [{"kind": "setting", "id": f"{args.experiment}/{n}", "address": f"{args.experiment}/{n}", **s.model_dump(mode="json")} for n, s, _ in rows]},
                     "outcome": {"passed": not failures, "failures": failures}})
    return EXIT_CHECK_FAILED if failures else EXIT_OK


def _failure(row: dict) -> dict:
    return {"code": row.get("code"), "subject": row["setting"], "reason": row.get("reason"), "candidates": row.get("candidates") or [],
            "fix": "point --source at the line or key that states it, give --term with the word the file uses, or set the value the source states"}


def _say_row(out: Any, r: dict) -> None:
    if r.get("skipped"):
        out.say(f"  {r['setting']}: {r['reason']}")
        return
    if r["ok"]:
        at = f" @{r['commit'][:7]}" if r.get("commit") else " (uncommitted file)"
        out.say(f"  {r['setting']}: verified in {r.get('where', '')}{at}, reads {r['read']!r}")
        return
    out.say(f"  {r['setting']}: {r['after']}{' · CONFLICT' if r.get('conflict') else ''}, not verified ({r['code']}): {r['reason']}")
    for c in r.get("candidates") or []:
        out.say(f"      line {c['line']}: {c['text']}")


def cmd_spec_verify(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    rows = led.verify_settings(args.experiment, args.names or None)
    if not rows:
        out.say(f"{args.experiment} has no setting with a source to check.")
    for r in rows:
        _say_row(out, r)
    failures = [_failure(r) for r in rows if not r["ok"] and not r.get("skipped")]
    verified = sum(1 for r in rows if r["ok"] and not r.get("skipped"))
    out.say(f"{verified} verified · {len(failures)} failed · {sum(1 for r in rows if r.get('skipped'))} not checkable")
    _actor_line(out, led)
    out.data.update({"experiment_id": args.experiment, "results": rows, "verified_count": verified,
                     "outcome": {"passed": not failures, "failures": failures}})
    return EXIT_CHECK_FAILED if failures else EXIT_OK


def cmd_spec_vary(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    names = [n.strip() for x in args.names for n in x.split(",") if n.strip()]
    exp, new = led.declare_varies(args.experiment, names, amend=_amend(args))
    out.say(f"{exp.id} varies {', '.join(exp.varies)} on purpose; a difference in any other setting is a confound.")
    if any(e.experiment == exp.id and e.retracted is None for e in led.all("evidence")):
        out.say("  The spec changed: evidence attached before this no longer counts toward its claims.")
    _actor_line(out, led)
    out.data.update({"object": {"kind": "experiment", **exp.model_dump(mode="json")}, "declared": new})
    return EXIT_OK


def cmd_claim_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    given = [(c, v) for c, v in (("at_most", args.at_most), ("at_least", args.at_least), ("equals", args.equals)) if v is not None]
    if len(given) != 1:
        raise RBError("E_OBJECT_INVALID", message="A claim needs exactly one criterion: --at-most X, --at-least X, or --equals X --tolerance T.")
    comparator, target = given[0]
    source = parse_source(args.source, args.quote, args.commit, args.locator, args.term, led.root)
    claim = led.add_claim(args.statement, metric=args.metric, comparator=comparator, target=target, tolerance=args.tolerance,
                          experiment=args.experiment, hypothesis=args.hypothesis, source=source, over=args.over, min_n=args.min_n,
                          noise=args.noise, note=args.note, amend=_amend(args), id=args.id)
    v = led.verdict(claim)
    out.say(f"{claim.id}: {claim.statement}", f"  {claim.criterion()} · {v.status}" + (f" · on {claim.experiment}" if claim.experiment else " · not on an experiment yet"))
    if claim.source is not None:
        out.say(f"  cited: {claim.source.label()} · " + (f"states {claim.target:g} (read {claim.source.resolved.text!r})" if claim.source.resolved else "recorded, not checked"))
    if not claim.created_by.startswith("human:"):
        out.say(f"  Written by {claim.created_by}: it can be supported, and it is established only once a person freezes {claim.experiment or 'its experiment'} with it in place.")
    _actor_line(out, led)
    out.data.update({"object": _obj("claim", claim), "verdict": v.model_dump(mode="json")})
    if claim.experiment:
        out.next = [led._attach_hint(claim)]
    return EXIT_OK


def _resolve_run_arg(arg: str, runs_dir_flag: Optional[str], led: Ledger) -> tuple[Any, Optional[Path]]:
    try:
        return resolve_run(arg, runs_dir(runs_dir_flag))
    except RBError as err:
        if err.code != "E_RUN_NOT_FOUND" or runs_dir_flag or not (led.root / "rb-runs").is_dir():
            raise
        return resolve_run(arg, led.root / "rb-runs")


def cmd_evidence_attach(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    pairs = split_pairs(args.pairs) + list(args.metric or [])
    bad = [t for t in args.pairs or [] if "=" not in t]
    if bad:
        raise RBError("E_USAGE", message=f"Numbers are NAME=VALUE (got {', '.join(bad)}).")
    metrics = parse_pairs(pairs, "metric", numeric=True)
    links = parse_pairs(args.link, "--link", numeric=False)
    per_run = parse_pairs(args.set, "--set", numeric=False)
    files = [root_relative(f, led.root) for f in args.artifact or []]
    basis = "typed"
    if args.from_file:
        nums, skipped = metrics_from_file(Path(args.from_file))
        overlap = sorted(set(nums) & set(metrics))
        if overlap:
            raise RBError("E_OBJECT_INVALID", message=f"NAME=VALUE and --from both give {', '.join(overlap)}.")
        metrics.update(nums)
        files.append(root_relative(args.from_file, led.root))
        basis = "file" if not pairs else "typed"
        if skipped:
            out.warn(f"{args.from_file}: not numbers, so not metrics: {', '.join(k or '(top level)' for k in skipped)}")
    run = None
    if args.run:
        bundle, run_dir = _resolve_run_arg(args.run, args.runs_dir, led)
        given = Path(args.run) if Path(args.run).is_file() else None
        run = evidence_from_run(bundle, run_dir, led.root, given=given)
    config = root_relative(args.config, led.root) if args.config else None
    result = led.attach_evidence(args.experiment, metrics=metrics, variant=args.variant, run=run, files=files, links=links, command=args.command_text,
                                 per_run=per_run, config=config, commit=args.commit, basis=basis, again=_again(args), note=args.note)
    ev = result["evidence"]
    if result["duplicate_of"]:
        rec = (ev.receipt.run_record or {}).get("record_sha256")
        out.say(f"Already attached as {ev.id}" + (f" (same run record {rec[:12]})" if rec else " (same numbers and files)") + "; nothing written. To attach a deliberate repeat: --again --why \"...\"")
        _actor_line(out, led)
        out.data.update({"object": _obj("evidence", ev), "duplicate_of": ev.id})
        return EXIT_OK
    out.say(f"{ev.id} on {ev.experiment}: " + ", ".join(f"{k}={v:g}" for k, v in sorted(ev.metrics.items())) + (f" · {', '.join(f'{k}={show(v)}' for k, v in ev.per_run.items())}" if ev.per_run else ""))
    if result["warning"]:
        out.warn(result["warning"])
    if ev.synthetic:
        out.say("  Synthetic: rb's example data or its synthetic adapter. It is shown and never counted.")
    if run is not None and run["record"].get("note"):
        out.say(f"  {run['record']['note']}.")
    cfg = ev.receipt.config
    if cfg is not None:
        if cfg.mismatches:
            out.say("  Ran with a different spec: " + "; ".join(f"{m['name']} {show(m['ran'])} (spec {show(m['spec'])})" for m in cfg.mismatches) + ". It will not count.")
        elif cfg.matches:
            out.say(f"  Ran with the spec: {len(cfg.matches)} of its settings found in {cfg.path}, all matching"
                    + (f" ({len(cfg.absent)} not mentioned there)." if cfg.absent else "."))
        else:
            out.say(f"  {cfg.path} mentions none of the spec's settings, so nothing was compared: the run's config is unchecked.")
    git = ev.receipt.attached
    if not git:
        out.say("  Not in a git repository: the receipt has no commit.")
    elif git.get("commit") is None:
        out.say("  The repository has no commit yet; the receipt records the uncommitted tree's hash.")
    elif git.get("dirty"):
        out.say(f"  The tree had uncommitted changes when attached (hash {git.get('diff_sha256', '')[:12]}).")
    claims = [c for c in led.live("claim") if c.experiment == ev.experiment]
    verdicts = [led.verdict(c) for c in claims]
    for v in verdicts:
        out.say(f"  {v.claim} {_standing(v.model_dump(mode='json'))}: {v.criterion}")
    _actor_line(out, led)
    unread = led.unread_metrics(led.load("experiment", ev.experiment), [ev], claims)
    out.data.update({"object": _obj("evidence", ev), "verdicts": [v.model_dump(mode="json") for v in verdicts], "unclaimed_metrics": unread})
    from .ledger import RUN_COUNTS
    pick = sorted(ev.metrics, key=lambda k: (k in RUN_COUNTS, not k.startswith("change."), k))[0]
    out.next = [f"rb show {v.claim}" for v in verdicts][:3] or [f'rb claim add "<what it should show>" -e {ev.experiment} --metric {pick} --at-most <x>   (write the claim before the next run)']
    return EXIT_OK


def _short(text: str, width: int = 16) -> str:
    """A long value (a checkpoint hash) in a table cell: its start, enough to tell the rows apart."""
    return text if len(text) <= width else text[: width - 1] + "…"


def _again(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "again", False):
        if not args.why:
            raise RBError("E_OBJECT_INVALID", message="--again needs --why \"<reason>\": a deliberate repeat says why.")
        return args.why
    return None


def cmd_retract(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    why = args.why or getattr(args, "reason", None)
    if not why:
        raise RBError("E_USAGE", message="rb retract <id> --why \"<reason>\"")
    subject = args.subject
    exp_id = None
    kind0 = led.kind_of(parse_address(subject)[0])
    if kind0 == "evidence":
        exp_id = led.load("evidence", subject).experiment
    elif kind0 == "claim":
        exp_id = led.load("claim", subject).experiment
    elif kind0 == "experiment":
        exp_id = parse_address(subject)[0]
    before = {c.id: led.verdict(c) for c in led.live("claim") if exp_id and c.experiment == exp_id}
    kind, sid, deps = led.retract(subject, why, handoff=out.command_line)
    out.say(f"{sid} ({kind}) retracted by {led.actor().id}: {why}. It stays on record and in the log, and no longer counts.")
    changes = []
    for c in led.live("claim"):
        if c.id in before:
            after = led.verdict(c)
            b = before[c.id]
            if (b.status, b.established) != (after.status, after.established):
                changes.append({"claim_id": c.id, "before": _standing(b.model_dump(mode="json")), "after": _standing(after.model_dump(mode="json"))})
                out.say(f"  {c.id}: {_standing(b.model_dump(mode='json'))} -> {_standing(after.model_dump(mode='json'))}")
    _actor_line(out, led)
    out.data.update({"object": {"kind": kind, "id": sid, "retracted": {"why": why}}, "rested_on": deps, "verdict_changes": changes})
    return EXIT_OK


def cmd_freeze(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    amend = getattr(args, "amend", False)
    if amend and not args.why:
        raise RBError("E_OBJECT_INVALID", message="rb freeze --amend needs --why \"<reason>\": the reason is what the amendment records.")
    fr = led.freeze(args.experiment, why=args.why or "", handoff=out.command_line, amend=amend)
    exp = led.load("experiment", args.experiment)
    claims = [c for c in led.claims_of(exp.id) if c.retracted is None]
    if amend:
        am = exp.amendments[-1]
        out.say(f"{exp.id} amended: the freeze record now matches it as it is ({am.before[:12]} -> {am.after[:12]}) · {am.why}")
    else:
        out.say(f"{exp.id} frozen: spec {fr.sha256[:12]}" + (f" · {fr.why}" if fr.why else ""))
    out.say(*freeze_review(led, exp))
    if not amend:
        out.say("  Changing a setting or its source, a variant, what it varies, or a claim now needs a person's --amend --why \"<reason>\", and is kept.")
        agents = [c.id for c in claims if not c.created_by.startswith("human:")]
        if fr.after_evidence and agents:
            out.say(f"  {exp.id} already has evidence ({', '.join(fr.after_evidence)}). For {', '.join(agents)}, written by an agent, it stays "
                    "exploratory: their criteria are fixed now, so only runs attached from here count.")
    unknown = [n for n, s in exp.all_settings() if s.value is None and s.required and not s.per_run]
    if unknown:
        out.say(f"  Required settings still unknown: {', '.join(unknown)}. No claim on it is established until they are known.")
    _actor_line(out, led)
    out.data.update({"object": _obj("experiment", exp), "unknown_required": unknown})
    out.next = [f'rb evidence attach {exp.id} --from <metrics.json> --command "..."']
    return EXIT_OK


def _named_otherwise(name: str, s: Any) -> Optional[str]:
    """How a setting's source names it, when not by the setting's own name: the one judgement verification cannot make
    for a person, so it is shown to them when they decide."""
    src = s.source
    if src is None or s.status != "verified":
        return None
    if src.term:
        return f"matched by the words {src.term!r}"
    from .sources import key_steps
    key = src.key or (src.pointer if src.kind == "run" else None)
    if key and key_steps(str(key))[-1].lower() != name.split(".")[-1].lower():
        return f"read from {key}"
    return None


def freeze_review(led: Ledger, exp: Any) -> list[str]:
    """What freezing an experiment locks, the way a person should read it before they do."""
    lines = [f"  {exp.id}: {exp.title}", "    variants: " + ", ".join(f"{v.name} ({v.role})" for v in exp.live_variants())
             + (f" · varies {', '.join(exp.varies)}" if exp.varies else " · varies nothing declared")]
    look = []
    for c in [c for c in led.claims_of(exp.id) if c.retracted is None]:
        lines.append(f"    claim {c.id}: {c.criterion()}   (written by {c.created_by})")
        if not c.created_by.startswith("human:") and led.criterion_fixed_at(c, exp) is None:
            look.append(f"    look: {c.id} was written by {c.created_by}; freezing makes its criterion yours. Is it the test you mean?")
    for full, s in exp.all_settings():
        state = "per-run" if s.per_run else s.status
        where = s.source.label() if s.source else "no source"
        lines.append(f"    {full} = {show(s.value) if not s.per_run else '(each run)'} · {state} · {where}")
        why = _named_otherwise(full, s)
        if why:
            look.append(f"    look: {full} is verified, {why}: is that this setting?")
        if s.conflict is not None:
            look.append(f"    look: {full} conflicts with its source: {s.conflict.text}")
    unknown = [n for n, s in exp.all_settings() if s.value is None and s.required and not s.per_run]
    if unknown:
        look.append(f"    look: unknown and required: {', '.join(unknown)}")
    return lines + look


def request_review(led: Ledger, r: Any) -> list[str]:
    """What an approved request would do, computed now, not described by whoever asked."""
    from .ledger import parse_address
    argv = r.argv
    lines = [f"{r.id}: {r.created_by} asks: {shlex.join(['rb', *argv])}"] + ([f"  why: {r.why}"] if r.why and r.why not in argv else [])
    try:
        if argv[0] == "freeze" and len(argv) > 1:
            exp = led.load("experiment", argv[1])
            if "--amend" in argv:
                lines.append(f"  adopts {exp.id} as it is now; its record says {(led.recorded_sha(exp) or '')[:12]}, it is {led.freeze_sha(exp)[:12]}")
            lines += freeze_review(led, exp)
        elif argv[0] == "decide" and len(argv) > 2:
            subject = argv[1]
            exp_id, setting = parse_address(subject)
            if setting is not None:
                exp, name, s = led.resolve_setting(subject)
                lines.append(f"  {subject} = {show(s.value)} · {s.status} · {s.source.label() if s.source else 'no source'}")
            else:
                kind, obj = led.get(subject)
                if kind == "claim":
                    v = led.verdict(obj)
                    lines.append(f"  {obj.id}: {obj.statement} · {_standing(v.model_dump(mode='json'))} · {obj.criterion()}")
                else:
                    lines.append(f"  {kind} {obj.id}: {getattr(obj, 'statement', None) or getattr(obj, 'text', None) or getattr(obj, 'title', '')}")
        elif argv[0] == "retract" and len(argv) > 1:
            lines.append(f"  takes back {argv[1]}; it stays on record and stops counting")
        elif "--adopt" in argv:
            lines += [f"  keeps {t['path']} as it stands: {t['why']}" for t in led.tampered()]
        elif "--amend" in argv:
            lines.append("  changes a frozen experiment; the change, the reason and the hash before and after are kept")
    except RBError as e:
        lines.append(f"  cannot be run as it stands: {e.message}")
    return lines


def cmd_approve(args: argparse.Namespace, out: Any) -> int:
    """The person's side of a handoff: every request an agent queued, reviewed and run as the person, or declined."""
    from .cli import main as rb_main
    led = Ledger.open()
    who = led.actor()
    if not who.is_person:
        raise RBError("E_HUMAN_ONLY", message=f"Approving a request is a person's call, and rb records you as {who.id} ({who.via}).")
    pending = led.pending_requests()
    targets = pending if args.id is None else [led.load_request(args.id)]
    if not targets:
        out.say("No requests waiting.")
    results = []
    interactive = args.id is None and not out.json_mode and sys.stdin.isatty()

    def show_now(*lines: str) -> None:        # in a terminal, the review must be on screen before the question
        if interactive:
            print("\n".join(lines), flush=True)
        else:
            out.say(*lines)

    for r in targets:
        show_now(*request_review(led, r), "")
        if r.status != "pending":
            show_now(f"  already {r.status}.")
            continue
        if args.id is None and not interactive:
            continue
        from .ledger import is_person_call
        if not is_person_call(r.argv) and not args.decline:   # a request file written by hand can say anything
            show_now(f"  {r.id} is not a person's call rb can run: rb {shlex.join(r.argv)}. Decline it with --decline.", "")
            results.append({"request": r.id, "status": "pending", "refused": "not a person's call"})
            continue
        choice = "n" if args.decline else "y"
        if interactive:
            choice = (input(f"Approve {r.id}? [y]es / [n]o / [s]kip: ").strip().lower() or "s")[0]
        if choice == "n":
            why = args.why or (input("  why (recorded): ").strip() if interactive else "")
            led.resolve_request(r.id, approved=False, why=why)
            show_now(f"  {r.id} declined.", "")
            results.append({"request": r.id, "status": "declined"})
        elif choice == "y":
            stdout, stderr = io.StringIO(), io.StringIO()     # the command's own output, reduced to its outcome
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = rb_main([*r.argv, "--json"])
            try:
                env = json.loads(stdout.getvalue())
            except ValueError:
                env = {"ok": False, "errors": [{"message": stderr.getvalue().strip()[-400:]}]}
            if code == 0:
                led = Ledger.open()
                led.resolve_request(r.id, approved=True, why=args.why or "", exit_code=code)
                show_now(f"  {r.id} approved: rb {shlex.join(r.argv)} ran as {led.actor().id}.", "")
                results.append({"request": r.id, "status": "approved", "exit_code": code, "result": env})
            else:
                errs = "; ".join(e.get("message", "") for e in env.get("errors", [])) or "see rb status"
                show_now(f"  {r.id} did not run: {errs}", "  It stays pending; decline it with --decline if it no longer applies.", "")
                results.append({"request": r.id, "status": "pending", "exit_code": code, "result": env})
    if args.id is None and not interactive and pending:
        out.next = [f"rb approve {r.id}" for r in pending[:3]]
    out.data.update({"pending_count": len(led.pending_requests()), "requests": [r.model_dump(mode="json") for r in targets], "results": results})
    return EXIT_OK


def cmd_decide(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    d = led.decide(args.subject, args.outcome, args.why, handoff=out.command_line)
    against = f" (verdict then: {d.verdict})" if d.verdict else ""
    word = d.outcome
    kind = "setting" if "/" in d.subject else led.kind_of(d.subject)
    if kind == "setting" and d.outcome == "accept":
        word = "vouched for"
    elif kind == "assumption":
        word = {"accept": "assumed", "reject": "violated", "investigate": "open"}[d.outcome]
    out.say(f"{d.id}: {word} {d.subject} by {d.by}{against}: {d.why}")
    if d.verdict and ((d.outcome == "accept" and d.verdict not in SETTLED) or (d.outcome == "reject" and d.verdict in SETTLED)):
        out.say(f"  This goes against the verdict ({d.verdict}). It is recorded as made; rb status shows both.")
    if kind == "hypothesis" and not [c for c in led.live("claim") if c.hypothesis == d.subject]:
        out.say(f"  {d.subject} has no claims or evidence: this decision rests on no evidence rb holds.")
    if d.outcome == "investigate":
        out.say("  It stays open, marked investigating, until someone accepts or rejects it.")
    _actor_line(out, led)
    out.data["object"] = _obj("decision", d)
    out.next = ["rb status"]
    return EXIT_OK


REASONS = {
    "criterion_not_fixed_by_person": "criterion not fixed by a person", "provisional": "unchecked settings", "unknown": "unknown settings",
    "conflict": "a source contradicts a setting", "stale": "a source changed", "confound": "a confound", "drift": "the frozen spec changed",
    "too_few_runs": "too few runs", "borderline": "within the noise", "edited_outside_rb": "edited outside rb",
    "cited_unchecked": "the cited number was not checked", "cited_source_changed": "the cited source changed", "exploratory_only": "only exploratory runs",
    "inherited_without_parent": "inherited without a parent", "rejected_setting": "a setting a person rejected", "not_comparable": "settings differ from the source's",
}


def _standing(v: dict) -> str:
    s = v["status"].replace("_", " ")
    if v["status"] in SETTLED:
        if v.get("established"):
            return s + " · established"
        why = v.get("not_established_because") or []
        s += " · not established" + (f": {REASONS.get(why[0], why[0].replace('_', ' '))}" if why else "") + (f" and {len(why) - 1} more" if len(why) > 1 else "")
    return s


def _stats(v: dict) -> str:
    obs = v.get("observations") or []
    conf = [o for o in obs if o["role"] == "confirmatory"]
    if not conf:
        other = [o for o in obs if o["role"] != "confirmatory"]
        return "no confirmatory evidence" + (f" · {len(other)} exploratory or not counted" if other else "")
    if len(conf) == 1:
        o = conf[0]
        return f"observed {o['value']:g} ({o['evidence']}, {o['basis']}) · 1 run (no repeat)"
    sd = f" · sd {v['sd']:.4g}" if v.get("sd") is not None else ""
    return f"n={v['n']} · mean {v['mean']:.4g}{sd} · {v['holding']}/{v['n']} hold"


def _verdict_block(v: dict, limit: Optional[int] = None) -> list[str]:
    lines = [f"  {v['claim']:<7} {v['statement']}", f"          {_standing(v)} · {v['criterion']} · {_stats(v)}"]
    cav = [c for c in v.get("caveats") or [] if c["blocks"]] + [c for c in v.get("caveats") or [] if not c["blocks"] and c["code"] != "single_run"]
    shown = cav if limit is None else cav[:limit]
    for c in shown:
        lines.append(f"          {'✗' if c['blocks'] else '·'} {c['text']}")
    if len(shown) < len(cav):
        lines.append(f"          and {len(cav) - len(shown)} more: rb show {v['claim']}")
    if v.get("decision"):
        lines.append(f"          decided: {v['decision']}")
    return lines


def _settings_counts(e: dict) -> str:
    k = e["settings_count"]
    s = f"settings {k['verified']} verified · {k['provisional']} provisional · {k['unknown']} unknown"
    if k.get("inherited"):
        s += f" · {k['inherited']} inherited"
    if k.get("per_run"):
        s += f" · {k['per_run']} per-run"
    return s


def _items_text(items: list[dict]) -> list[str]:
    """Open items, with many of one kind collapsed into one line and the command under each."""
    out, groups = [], {}
    for it in items:
        groups.setdefault((it["who"], it["code"]), []).append(it)
    for (who, code), its in groups.items():
        if len(its) > 3 and who != "person":       # many of one kind: one line, and each one's own command under it
            out.append(f"  - {len(its)} × {code.replace('_', ' ')}:")
            for it in its[:6]:
                out.append(f"      {it['do']}")
            if len(its) > 6:
                out.append(f"      ... and {len(its) - 6} more: rb status --json lists every one")
        else:
            for it in its:
                out.append(f"  - {it['what']}")
                out.append(f"      {it['do']}")
    return out


def cmd_status(args: argparse.Namespace, out: Any) -> int:
    fail_on = [g.strip() for item in (args.fail_on or []) for g in item.split(",") if g.strip()]
    bad = [g for g in fail_on if g not in GATES]
    if bad:
        raise RBError("E_OBJECT_INVALID", message=f"--fail-on takes {', '.join(GATES)} (got {', '.join(bad)}).")
    led = Ledger.open()
    st = led.status(args.experiment)
    inv = st["investigation"]
    tally = " · ".join(f"{n} {k}" for k, n in sorted(st["tally"].items(), key=lambda kv: _tally_order(kv[0])))
    out.say(f"{inv['title']} · {inv['id']}" + (f" · experiment {args.experiment}" if args.experiment else ""),
            f"Claims: {tally}" if tally else "Claims: none yet",
            f"You are recorded as {st['actor']['id']} ({st['actor']['via']})")
    person = [i for i in st["open"] if i["who"] == "person"]
    agent = [i for i in st["open"] if i["who"] != "person"]
    if not st["experiments"] and not st["claims"] and not st["questions"]:
        out.say("", 'Nothing recorded yet. Start: rb experiment add "<what you are testing>" --baseline <name> --candidate <name>')
    if person:
        out.say("", f"Needs a person ({len(person)})", *_items_text(person))
    if agent:
        out.say("", f"Agent can do ({len(agent)})", *_items_text(agent))
    if st["claims"]:
        out.say("", "Claims")
        for v in st["claims"]:
            out.say(*_verdict_block(v, STATUS_CAVEATS))
    if st["experiments"]:
        out.say("", "Experiments")
        for e in st["experiments"]:
            arms = " vs ".join(f"{v['name']} ({v['role']})" for v in e["variants"]) or "no variants"
            frozen = (f"frozen {e['frozen']['sha256'][:12]}" + (f", amended {e['amendment_count']}x" if e["amendment_count"] else "")) if e["frozen"] else "not frozen"
            if e["spec_drift"]:
                frozen += ", SPEC CHANGED WITHOUT AN AMENDMENT"
            out.say(f"  {e['id']:<7} {e['title']} · {arms}" + (f" · varies {', '.join(e['varies'])}" if e["varies"] else "") + f" · {frozen} · {_settings_counts(e)} · evidence {e['evidence_count']}")
    if not st["open"] and (st["claims"] or st["experiments"]):
        out.say("", "Nothing open.")
    tripped = [g for g in fail_on if st["gate"][g]]
    edited = any(c["code"] == "edited_outside_rb" for v in st["claims"] for c in v.get("caveats") or [])
    if fail_on and edited and not tripped:
        tripped = ["edited_outside_rb"]
    st["fail_on"] = fail_on
    st["outcome"] = {"passed": not tripped, "failures": [{"code": g, "reason": f"--fail-on {g}"} for g in tripped]}
    if tripped:
        out.say("", f"GATE FAILED on: {', '.join(tripped)}.")
    out.data.update(st)
    out.next = [i["do"] for i in st["open"][:3]]
    return EXIT_CHECK_FAILED if tripped else EXIT_OK


def _tally_order(key: str) -> int:
    order = ["established", "supported, not established", "reproduced, not established", "mixed", "refuted", "not_reproduced", "not_comparable", "untested", "inherited"]
    return order.index(key) if key in order else len(order)


def cmd_show(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    subject = args.id
    exp_id, name = parse_address(subject)
    if name is not None or ("." in subject and led.kind_of(subject) is None and led.kind_of(subject.split(".")[0]) == "experiment"):
        return _show_setting(led, subject, out)
    kind, obj = led.get(subject)
    data: dict[str, Any] = {"object": _obj(kind, obj)}
    if kind == "claim":
        v = led.verdict(obj)
        data["verdict"] = v.model_dump(mode="json")
        out.say(f"Claim {obj.id} ({obj.origin}, written by {obj.created_by}): {obj.statement}")
        if obj.source:
            out.say(f"  cited: {obj.source.label()}" + (f" · states {obj.target:g}, read {obj.source.resolved.text!r}" if obj.source.resolved else " · not checked"))
        out.say(*_verdict_block(data["verdict"]))
        for o in v.observations:
            out.say(f"  {o.evidence}: {obj.metric} = {o.value:g} ({o.basis}) · {'holds' if o.holds else 'misses'} by {abs(o.margin):g} · {o.role.replace('_', ' ')}"
                    + (f": {o.reason}" if o.reason else "") + (f" · {', '.join(f'{k}={show(x)}' for k, x in o.per_run.items())}" if o.per_run else ""))
    elif kind == "experiment":
        _show_experiment(led, obj, out, data)
    elif kind == "evidence":
        out.say(f"Evidence {obj.id} on {obj.experiment} ({obj.basis}{', synthetic' if obj.synthetic else ''}) · attached {obj.created_at} by {obj.created_by}")
        out.say(*[f"  {k} = {x:g}" for k, x in sorted(obj.metrics.items())])
        if obj.per_run:
            out.say("  per-run: " + ", ".join(f"{k}={show(x)}" for k, x in obj.per_run.items()))
        if obj.run:
            out.say(f"  run: {obj.run}")
        r = obj.receipt
        if r.command:
            out.say(f"  command (as given): {r.command}")
        out.say(f"  produced: {(r.produced.get('commit') or '')[:12] or 'commit unknown'} ({r.produced.get('from')})")
        if r.attached:
            out.say(f"  repository when attached: {(r.attached.get('commit') or 'no commit yet')[:12]}{' with uncommitted changes' if r.attached.get('dirty') else ''}")
        if r.config:
            out.say(f"  config {r.config.path}: {len(r.config.matches)} match, {len(r.config.mismatches)} differ, {len(r.config.absent)} absent")
        for f in obj.files:
            out.say(f"  file {f.path} sha256 {f.sha256[:12]} ({f.bytes} bytes)")
        for k, url in obj.links.items():
            out.say(f"  {k}: {url}")
    else:
        text = getattr(obj, "text", None) or getattr(obj, "statement", None) or getattr(obj, "why", None) or getattr(obj, "description", "") or ""
        status = getattr(obj, "status", None) or getattr(obj, "outcome", None) or getattr(obj, "direction", "")
        out.say(f"{kind.capitalize()} {obj.id} ({status}): {text}")
        if kind == "decision":
            out.say(f"  on {obj.subject} by {obj.by} at {obj.at}" + (f" · verdict then: {obj.verdict}" if obj.verdict else ""))
    retracted = getattr(obj, "retracted", None)
    if retracted:
        out.say(f"  RETRACTED {retracted.at} by {retracted.by}: {retracted.why}")
    decisions = led.decisions_on(obj.id)
    if decisions:
        data["decisions"] = [d.model_dump(mode="json") for d in decisions]
        out.say(*[f"  decision {d.id}: {d.outcome} by {d.by} at {d.at}: {d.why}" for d in decisions])
    out.data.update(data)
    return EXIT_OK


def _show_setting(led: Ledger, subject: str, out: Any) -> int:
    exp, name, s = led.resolve_setting(subject)
    address = f"{exp.id}/{name}"
    out.say(f"{address} · per-run: each piece of evidence gives its own" if s.per_run else
            f"{address} = {show(s.value)} · {s.status}{'' if s.required else ' (optional)'}")
    if s.source:
        out.say(f"  source: {s.source.label()}" + (f" · read {s.source.resolved.text!r}" + (f" @{s.source.resolved.commit[:7]}" if s.source.resolved.commit else "") if s.source.resolved else ""))
    if s.conflict:
        out.say(f"  CONFLICT: {s.conflict.text}")
    if s.cited:
        out.say(f"  cited source used {show(s.cited.value)}" + (f" ({s.cited.source.label()})" if s.cited.source else ""))
    history = [r for r in led.log_entries(about=address) if r.get("op") in ("spec_set", "spec_verify", "retract")]
    if history:
        out.say("  history:")
        for r in history:
            d = r.get("detail") or {}
            out.say(f"    {str(r.get('at', ''))[:19]} {r.get('actor')} {r.get('op')}: {d.get('change') or d.get('outcome') or d.get('why') or ''}")
    for d in led.decisions_on(address):
        out.say(f"  decision {d.id}: {d.outcome} by {d.by}: {d.why}")
    out.data.update({"object": {"kind": "setting", "id": address, "address": address, "experiment_id": exp.id, **s.model_dump(mode="json")}, "history": history})
    return EXIT_OK


def _show_experiment(led: Ledger, exp: Experiment, out: Any, data: dict) -> None:
    out.say(f"Experiment {exp.id}: {exp.title}")
    if exp.hypotheses:
        out.say(f"  tests: {', '.join(exp.hypotheses)}")
    if exp.variants:
        out.say("  variants: " + " · ".join(f"{v.name} ({v.role})" + (" RETRACTED" if v.retracted else "") for v in exp.variants))
    if exp.varies:
        out.say(f"  varies: {', '.join(exp.varies)}")
    out.say(f"  spec {led.freeze_sha(exp)[:12]} · " + (f"frozen {exp.frozen.at} by {exp.frozen.by} ({exp.frozen.sha256[:12]})" + (f": {exp.frozen.why}" if exp.frozen.why else "") if exp.frozen else "not frozen"))
    drift = led.spec_drift(exp)
    if drift:
        out.say(f"  {drift}")
    for a in exp.amendments:
        out.say(f"  amended {a.at} by {a.by}: {a.change} · because: {a.why}")
    caveats = led.setting_caveats(exp, led.investigation.parent is not None)
    flags = {c.subject.split("/", 1)[1]: c.code for c in caveats if c.code in ("stale", "conflict", "provisional", "unknown", "vouched")}
    if exp.all_settings():
        out.say("  settings:")
        for full, s in exp.all_settings():
            where = f"{s.source.label()}" if s.source else "no source"
            state = "per-run" if s.per_run else s.status + ("" if s.required else " (optional)")
            out.say(f"    {full:<24} {show(s.value) if not s.per_run else '(each run)':<14} {state} · {where}"
                    + (f" · {flags[full].upper()}" if flags.get(full) in ("stale", "conflict") else "") + (" · vouched" if flags.get(full) == "vouched" else ""))
    confounds = [c for c in caveats if c.code in ("confound", "confound_accepted", "varies_unchanged")]
    for c in confounds:
        out.say(f"  {'✗' if c.blocks else '·'} {c.text}")
    claims = [led.verdict(c) for c in led.claims_of(exp.id) if c.retracted is None]
    if claims:
        out.say("  claims:")
        for v in claims:
            out.say(*_verdict_block(v.model_dump(mode="json")))
    evidence = [e for e in led.all("evidence") if e.experiment == exp.id]
    if evidence:
        out.say("  evidence: " + ", ".join(f"{e.id}{' (retracted)' if e.retracted else ''}" for e in evidence))
    data.update({"freeze_sha256": led.freeze_sha(exp), "spec_sha256": led.spec_sha(exp), "spec_drift": drift,
                 "caveats": [c.model_dump() for c in caveats], "claims": [v.model_dump(mode="json") for v in claims], "evidence_ids": [e.id for e in evidence]})


def cmd_compare(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    exp_id = args.experiment
    if exp_id is None:
        exps = led.live("experiment")
        if len(exps) != 1:
            raise RBError("E_USAGE", message="rb compare <experiment>: " + (", ".join(e.id for e in exps) if exps else "no experiments yet"))
        exp_id = exps[0].id
    t = led.compare(exp_id)
    out.say(f"{t['experiment_id']}  {t['title']}" + (f" · varies {', '.join(t['varies'])}" if t["varies"] else "") + f" · {t['evidence_count']} evidence under the current spec")
    heads = ["variant", "role", *t["varies"]] + [m["name"] + (f" ({m['unit']})" if m["unit"] else "") + ("" if m["declared"] else "*") for m in t["metrics"]]
    rows = []
    for r in t["rows"]:
        cells = [r["variant"], r["role"], *[_short(show(r["varies"].get(n))) for n in t["varies"]]]
        for m in t["metrics"]:
            c = r["metrics"].get(m["name"])
            if c is None:
                cells.append("—")
                continue
            txt = f"{c['mean']:.4g}"
            if "delta" in c:
                mark = "" if c.get("better") is None else (" better" if c["better"] else " worse")
                txt += f" {c['delta']:+.3g}{mark}"
            cells.append(f"{txt} (n={c['n']})")
        rows.append(cells)
    widths = [max(len(str(x)) for x in col) for col in zip(heads, *rows)] if rows else [len(h) for h in heads]
    out.say("  " + "  ".join(h.ljust(w) for h, w in zip(heads, widths)))
    for cells in rows:
        out.say("  " + "  ".join(str(c).ljust(w) for c, w in zip(cells, widths)))
    if any(not m["declared"] for m in t["metrics"]):
        out.say("  * not in the catalogue: rb metric add <name> --unit <unit> --minimize|--maximize says which way is better")
    if t["overall"]:
        out.say("  whole-comparison numbers: " + ", ".join(f"{k}={v['mean']:.4g}" for k, v in t["overall"].items()))
    out.data.update(t)
    return EXIT_OK


def cmd_log(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    rows = led.log_entries(last=args.n, about=args.id)
    for r in rows:
        d = r.get("detail") or {}
        what = d.get("change") or d.get("outcome") or d.get("why") or d.get("criterion") or ""
        if r.get("op") == "spec_verify" and d.get("results"):
            ok = sum(1 for x in d["results"] if x.get("ok"))
            what = f"{ok} of {len(d['results'])} checked"
        stamp = f", RB_ACTOR={r['ignored_rb_actor']} ignored" if r.get("ignored_rb_actor") else ""
        out.say(f"{str(r.get('at', ''))[:16].replace('T', ' ')} {r.get('actor')} ({r.get('actor_via', '?')}{stamp}; via {r.get('via', '?')}) "
                f"{r.get('op')} {r.get('kind')} {r.get('id')}" + (f": {what}" if what else ""))
    if not rows:
        out.say("Nothing logged.")
    out.data["entries"] = rows
    return EXIT_OK


def cmd_research_doctor(args: argparse.Namespace, out: Any, led: Ledger) -> int:
    """What can go wrong with .rb/ that no single command would notice. It reads every file on its own, so one that does
    not parse is listed rather than stopping the check. --restore puts back what rb last wrote; --adopt (a person's
    call) takes the files as they stand."""
    from .actor import current
    from .ledger import KINDS
    from .sources import inside_project
    if getattr(args, "restore", False) and getattr(args, "adopt", False):
        raise RBError("E_USAGE", message="rb doctor takes --restore or --adopt, not both.")
    if getattr(args, "restore", False):
        restored = led.restore()
        for d in restored:
            out.say(f"restored {d['path']}: {d['done']}")
        if not restored:
            out.say("Nothing to restore: every file is what rb last wrote.")
        out.data["restored"] = restored
    if getattr(args, "adopt", False):
        if not getattr(args, "why", None):
            raise RBError("E_OBJECT_INVALID", message="rb doctor --adopt needs --why \"<reason>\": the reason is what the adoption records.")
        adopted = led.adopt(args.why)
        for d in adopted:
            out.say(f"adopted {d['path']}: {d['done']}")
        out.data["adopted"] = adopted
    a = led.actor() if led._agent else current(led.root)
    checks = [{"check": "actor", "ok": True, "detail": f"{a.id}, from {a.via}" + (f" (RB_ACTOR={a.ignored} is ignored inside {runtime_label(a.runtime)})" if a.ignored and a.runtime else "")}]
    problems = []
    fix_edit = 'rb doctor --restore puts back what rb last wrote; if the change is right, a person runs rb doctor --adopt --why "..."'
    parsed: dict[str, list] = {}
    files = [("investigation", led.dir / "investigation.json")] + [(k, p) for k, (d, _, _) in KINDS.items() for p in sorted((led.dir / d).glob("*.json"))]
    for kind, p in files:
        rel = p.relative_to(led.root)
        text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        if "<<<<<<<" in text or ">>>>>>>" in text:
            problems.append({"check": "merge_conflict", "ok": False, "detail": f"{rel} has conflict markers",
                             "fix": "resolve it by keeping one side as rb wrote it (the other side's write is in log.jsonl), then rb doctor again"})
            continue
        try:
            obj = led._read(p, KINDS[kind][2] if kind in KINDS else type(led.investigation))
        except RBError as e:
            problems.append({"check": "corrupt", "ok": False, "detail": f"{rel} is not a valid {kind}: {'; '.join(e.problems[:2]) or e.message}", "fix": fix_edit})
            continue
        parsed.setdefault(kind, []).append(obj)
    try:
        tamper = led.tampered()
    except RBError as e:
        tamper = []
        problems.append({"check": "log", "ok": False, "detail": e.message, "fix": "resolve the merge in .rb/log.jsonl by keeping every line from both sides"})
    for t in tamper:
        if not any(p["detail"].startswith(t["path"]) for p in problems):
            problems.append({"check": "edited_outside_rb", "ok": False, "detail": f"{t['path']}: {t['why']}", "fix": fix_edit})
    ids: dict[str, str] = {}
    for kind in ("question", "hypothesis", "assumption", "experiment", "claim", "evidence", "decision"):
        for obj in parsed.get(kind, []):
            if obj.id in ids:
                problems.append({"check": "duplicate_id", "ok": False, "detail": f"{obj.id} is both a {ids[obj.id]} and a {kind}", "fix": "retract one and add it again"})
            ids[obj.id] = kind
    for c in parsed.get("claim", []):
        if c.experiment and c.experiment not in ids:
            problems.append({"check": "dangling", "ok": False, "detail": f"claim {c.id} points at experiment {c.experiment}, which is missing", "fix": fix_edit})
    for e in parsed.get("experiment", []):
        for full, s in e.all_settings():
            if s.source is not None and s.source.path and s.source.kind in ("file", "run") and not inside_project(led.root, s.source.path):
                problems.append({"check": "outside", "ok": False, "detail": f"{e.id}/{full} reads {s.source.path}, outside the project", "fix": "copy it into the repository and set the source again"})
    checks += problems
    for c in checks:
        out.say(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}" + (f"\n     → {c['fix']}" if c.get("fix") else ""))
    out.say("", "No release-review config checked here; for that: rb review doctor")
    out.data.update({"checks": checks, "outcome": {"passed": not problems, "failures": problems}})
    return EXIT_CHECK_FAILED if problems else EXIT_OK


def context_markdown(led: Ledger, st: dict) -> str:
    """The handoff pack: what a fresh agent session (or a colleague) reads first instead of anyone's summary."""
    inv = st["investigation"]
    a = st["actor"]
    L = [f"# Research state: {inv['title']}", "",
         f"You are recorded as {a['id']} ({a['via']}). Generated by `rb context` from `.rb/`; read the state from here or `rb status --json`, never from memory or a summary.", "",
         "## Rules", "",
         "- You propose; rb verifies and computes; people decide. Add questions, hypotheses, experiments, variants, settings with their sources, claims and evidence.",
         "- Only `rb spec verify` (or `rb spec set` with a checkable source) makes a setting verified. Only rb computes a verdict. Never edit `.rb/` by hand.",
         "- Freezing, deciding, amending, and retracting what a person wrote or what something rests on are a person's calls. When rb refuses one it queues it: tell the person to run `rb approve` in their own terminal. Do not set or unset RB_ACTOR.",
         "- Write a claim before the run that tests it; evidence attached before its claim or before the freeze is exploratory and never counts.",
         "- Pass on every caveat and every unknown below with any result you report.", "",
         "## Words", "",
         "- supported: confirmatory evidence meets the criterion. reproduced: it meets a cited number.",
         "- established: supported or reproduced, and every blocking caveat cleared (a person fixed the criterion, settings verified or vouched for, no confound, no drift, enough runs, not borderline).",
         "- verified: the named file or run states this setting's value at that commit. It does not mean a run used it; `--config` on evidence checks that.",
         "- provisional: given, not checked. exploratory: evidence from before the claim or the freeze; shown, never counted.", "",
         "## Report it like this", "",
         "`c1 supported, not established: blocked on e1/seed unknown; 1 run (no repeat).` Quote the standing, then every blocking caveat. Never call a claim established unless it is listed under Established below.", ""]
    est = [v for v in st["claims"] if v["established"]]
    rest = [v for v in st["claims"] if not v["established"]]
    L += ["## Established", ""] + (["\n".join(_verdict_block(v)) for v in est] or ["- Nothing yet."]) + [""]
    L += ["## Not established", ""] + (["\n".join(_verdict_block(v)) for v in rest] or ["- Nothing."]) + [""]
    person = [i for i in st["open"] if i["who"] == "person"]
    agent = [i for i in st["open"] if i["who"] != "person"]
    L += ["## Needs a person", ""] + (_items_text(person) or ["- Nothing."]) + [""]
    L += ["## Agent can do", ""] + (_items_text(agent) or ["- Nothing."]) + [""]
    for e in led.live("experiment"):
        if st.get("experiment_id") and e.id != st["experiment_id"]:
            continue
        arms = " · ".join(f"{v.name} ({v.role})" for v in e.live_variants())
        L += [f"## Experiment {e.id}: {e.title}", ""]
        L.append(f"- {arms or 'no variants'}" + (f" · varies {', '.join(e.varies)}" if e.varies else "") + (f" · frozen {e.frozen.at[:10]} by {e.frozen.by}" + (f": {e.frozen.why}" if e.frozen.why else "") if e.frozen else " · not frozen"))
        if e.note:
            L.append(f"- note: {e.note}")
        if e.all_settings():
            L += ["", "| setting | value | status | checked in |", "|---|---|---|---|"]
            caveats = {c.subject.split("/", 1)[1]: c for c in led.setting_caveats(e, inv.get("parent") is not None) if c.code in ("stale", "conflict", "confound")}
            for full, s in e.all_settings():
                where = s.source.label() if s.source and s.status == "verified" else (f"(given: {s.source.label()})" if s.source else "")
                flag = f" · {caveats[full].code.upper()}" if full in caveats else ""
                L.append(f"| {full} | {show(s.value) if not s.per_run else '(each run)'} | {'per-run' if s.per_run else s.status}{flag} | {where} |")
        for am in e.amendments:
            L.append(f"- amended {am.at[:10]} by {am.by}: {am.change}, because \"{am.why}\"")
        for ev in led.live("evidence"):
            if ev.experiment != e.id:
                continue
            cfg = ev.receipt.config
            ran = ("config unchecked" if cfg is None or not (cfg.matches or cfg.mismatches)
                   else "ran with the spec" if not cfg.mismatches else "ran with a different spec")
            L.append(f"- {ev.id} ({ev.basis}{', synthetic' if ev.synthetic else ''}): " + ", ".join(f"{k}={x:g}" for k, x in sorted(ev.metrics.items())[:6])
                     + (f" · {', '.join(f'{k}={show(x)}' for k, x in ev.per_run.items())}" if ev.per_run else "")
                     + f" · {ran}" + (f" · command: {ev.receipt.command}" if ev.receipt.command else "")
                     + (f" · produced at {ev.receipt.produced.get('commit')[:12]}" if ev.receipt.produced.get("commit") else "") + (f" · {ev.note}" if ev.note else ""))
        L.append("")
    if st["questions"]:
        L += ["## Questions", ""] + [f"- {q['id']} ({q['status']}): {q['text']}" for q in st["questions"]] + [""]
    if st["hypotheses"]:
        L += ["## Hypotheses", ""] + [f"- {h['id']} ({h['status']}): {h['statement']}" + (f" Why: {h['why']}" if h.get("why") else "") for h in st["hypotheses"]] + [""]
    if st["assumptions"]:
        L += ["## Assumptions", ""] + [f"- {x['id']} ({x['status']}): {x['text']}" for x in st["assumptions"]] + [""]
    if st["decisions"]:
        L += ["## Decisions", ""] + [f"- {d['id']}: {d['outcome']} {d['subject']} by {d['by']}" + (f" (verdict then: {d['verdict']})" if d.get("verdict") else "") + f": {d['why']}" for d in st["decisions"]] + [""]
    recent = led.log_entries(last=40)
    if recent:
        L += ["## Recent activity", ""] + _collapse_log(recent)[-12:] + [""]
    return "\n".join(L)


def _collapse_log(rows: list[dict]) -> list[str]:
    out: list[str] = []
    prev_key, count = None, 0
    for r in rows:
        key = (r.get("actor"), r.get("op"), r.get("kind"), r.get("id"))
        if key == prev_key:
            count += 1
            out[-1] = f"- {str(r.get('at', ''))[:16].replace('T', ' ')} {r.get('actor')} {r.get('op')} {r.get('kind')} {r.get('id')} (x{count})"
            continue
        prev_key, count = key, 1
        d = r.get("detail") or {}
        what = d.get("change") or d.get("outcome") or d.get("why") or ""
        out.append(f"- {str(r.get('at', ''))[:16].replace('T', ' ')} {r.get('actor')} {r.get('op')} {r.get('kind')} {r.get('id')}" + (f": {what}" if what else ""))
    return out


def cmd_context(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    st = led.status()
    md = context_markdown(led, st)
    out.say(md.rstrip("\n"))
    out.data.update({"markdown": md, "state": st})
    out.next = [i["do"] for i in st["open"][:3]]
    return EXIT_OK


# ---------------------------------------------------------------- parsers


def add_parsers(sub: Any, common: argparse.ArgumentParser, research_common: argparse.ArgumentParser) -> None:
    """`research_common` carries --json, and --runs-dir/--verbose hidden: they mean nothing to the research state."""
    rc = [research_common]

    def group(name: str, verbs: str, help: str) -> Any:
        g = sub.add_parser(name, help=f"{verbs}: {help}")
        return g.add_subparsers(dest="sub_command", metavar=f"<{verbs}>", required=True)

    def src(s: argparse.ArgumentParser, what: str, quote_help: str) -> None:
        s.add_argument("--source", default=None, help=f"where {what} comes from: file#key (YAML/JSON/TOML), file:LINE, run:PATH#/pointer, https://..., or note:text")
        s.add_argument("--quote", default=None, help=quote_help)
        s.add_argument("--term", default=None, help="the word the source uses for it on a prose line, e.g. \"learning rate\"")
        s.add_argument("--commit", default=None, help="read the file at this git commit instead of the working tree")
        s.add_argument("--locator", default=None, help="how a reader finds it, e.g. \"§4.2\" or \"Table 3\"")

    def why(s: argparse.ArgumentParser, required: bool = False, help: str = "the reason, recorded with the call") -> None:
        s.add_argument("-m", "--why", required=required, default=None, help=help)

    g = group("question", "add", "a question the research is trying to answer")
    s = g.add_parser("add", parents=rc, help="add a question")
    s.add_argument("text")
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_question_add)

    g = group("hypothesis", "add", "what you expect, and why")
    s = g.add_parser("add", parents=rc, help="add a hypothesis")
    s.add_argument("statement")
    s.add_argument("-m", "--why", default="", help="the reasoning")
    s.add_argument("-q", "--question", default=None, help="the question it answers")
    s.add_argument("--expect", default=None, help=argparse.SUPPRESS)
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_hypothesis_add)

    g = group("assumption", "add", "something the work takes for granted")
    s = g.add_parser("add", parents=rc, help="add an assumption")
    s.add_argument("text")
    s.add_argument("-e", "--experiment", "--applies-to", dest="experiment", action="append", default=None, help="experiment it applies to (repeatable)")
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_assumption_add)

    g = group("experiment", "add", "what would test a hypothesis")
    s = g.add_parser("add", parents=rc, help="add an experiment with its variants and what it varies")
    s.add_argument("title")
    s.add_argument("--id", default=None, help="a short id you choose (default: e and four random characters)")
    s.add_argument("--hypothesis", "--tests", dest="hypothesis", action="append", default=None, help="hypothesis id it tests (repeatable)")
    s.add_argument("--baseline", default=None, help="name of the baseline variant, e.g. fp32")
    s.add_argument("--candidate", action="append", default=None, help="name of a candidate variant, e.g. int8 (repeatable)")
    s.add_argument("--varies", action="append", default=None, help="the setting(s) the variants differ in on purpose (repeatable or comma-separated); any other difference is a confound")
    s.add_argument("--like", default=None, help="copy another experiment's shared settings (as provisional) and what it varies")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_experiment_add)

    g = group("variant", "add", "an arm of an experiment: baseline, candidate, control, ablation")
    s = g.add_parser("add", parents=rc, help="add a variant to an experiment")
    s.add_argument("experiment")
    s.add_argument("name")
    s.add_argument("--role", required=True, choices=["baseline", "candidate", "control", "ablation"])
    s.add_argument("--note", default="")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: add it anyway (a person's call, with --why)")
    why(s)
    s.set_defaults(func=cmd_variant_add)

    g = group("metric", "add", "the catalogue: a metric's unit, which way is better, its other names")
    s = g.add_parser("add", parents=rc, help="declare a metric")
    s.add_argument("name")
    s.add_argument("--unit", default=None)
    d = s.add_mutually_exclusive_group()
    d.add_argument("--minimize", action="store_true", help="lower is better (an error, a loss, a latency)")
    d.add_argument("--maximize", action="store_true", help="higher is better (an accuracy, a score)")
    s.add_argument("--alias", action="append", default=None, help="another name evidence may use for it (repeatable), e.g. endpoint_error")
    s.add_argument("--description", default=None)
    s.set_defaults(func=cmd_metric_add)

    g = group("spec", "set|verify|vary", "an experiment's settings, where each value comes from, and whether rb has checked it")
    s = g.add_parser("set", parents=rc, help="give a setting's value and source (checked when it can be), record it as unknown, or read many from a config with --from")
    s.add_argument("experiment")
    s.add_argument("name", nargs="?", default=None, help="the setting; <variant>.<name> for one variant's own value")
    s.add_argument("value", nargs="?", default=None, help="a number, true/false, text, or a JSON list; optional with file#key or run: sources")
    s.add_argument("--unknown", action="store_true", help="nobody has found this value yet")
    s.add_argument("--per-run", action="store_true", help="each piece of evidence gives its own value (a seed): --set seed=2 on attach")
    src(s, "the value", "the exact text in a file or url source that states it")
    s.add_argument("--variant", default=None, help="set it for this variant only (same as <variant>.<name>)")
    req = s.add_mutually_exclusive_group()
    req.add_argument("--optional", action="store_true", help="an unknown or provisional value does not keep claims from being established")
    req.add_argument("--required", action="store_true", help="it does (the default for a new setting)")
    s.add_argument("--cited", default=None, metavar="VALUE", help="the value the cited source (a paper, its evaluation code) used; a difference makes cited claims not comparable")
    s.add_argument("--cited-source", default=None, help="where the cited value comes from")
    s.add_argument("--from", dest="from_file", default=None, metavar="FILE", help="record settings from a YAML/JSON/TOML config, each by key path, with --keys")
    s.add_argument("--keys", action="append", default=None, help="with --from: the keys that define the experiment, glob patterns, e.g. \"optim.*,model.depth\"")
    s.add_argument("--note", default=None)
    s.add_argument("--no-verify", action="store_true", help="record it without checking the source now")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: change it anyway (a person's call, with --why)")
    why(s)
    s.set_defaults(func=cmd_spec_set)
    s = g.add_parser("verify", parents=rc, help="read each setting's source; verified only if it states the value")
    s.add_argument("experiment")
    s.add_argument("names", nargs="*", help="settings (default: every setting with a source)")
    s.set_defaults(func=cmd_spec_verify)
    s = g.add_parser("vary", parents=rc, help="declare settings the variants differ in on purpose; any other difference is a confound")
    s.add_argument("experiment")
    s.add_argument("names", nargs="+", help="setting names (or comma-separated)")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: change it anyway (a person's call, with --why)")
    why(s)
    s.set_defaults(func=cmd_spec_vary)

    g = group("claim", "add", "a statement with a criterion evidence can meet or miss")
    s = g.add_parser("add", parents=rc, help="add a claim; write it before the run that tests it")
    s.add_argument("statement")
    s.add_argument("-e", "--experiment", required=True, help="the experiment whose evidence tests it")
    s.add_argument("--metric", required=True, help="what the evidence reports: epe, int8.epe, candidate.epe, or change.epe (candidate minus baseline)")
    s.add_argument("--at-most", type=float, default=None)
    s.add_argument("--at-least", type=float, default=None)
    s.add_argument("--equals", "--within", dest="equals", type=float, default=None, metavar="TARGET", help="with --tolerance: holds when |observed - TARGET| <= tolerance")
    s.add_argument("--tolerance", type=float, default=None)
    s.add_argument("--over", choices=["each", "mean"], default="each", help="each run must meet it (default), or their mean")
    s.add_argument("--min-n", type=int, default=1, help="confirmatory runs it takes to be established (default 1)")
    s.add_argument("--noise", type=float, default=None, help="run-to-run spread: a margin smaller than this is borderline")
    s.add_argument("--hypothesis", default=None)
    src(s, "the cited number (a paper's table)", "the exact text that states the number; a file source must state it or the claim is refused")
    s.add_argument("--note", default="")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: add it anyway (a person's call, with --why)")
    why(s)
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_claim_add)

    g = group("evidence", "attach", "numbers from a run, with a receipt")
    s = g.add_parser("attach", parents=[common], help="attach numbers from any evaluator, a file, or an rb run to an experiment")
    s.add_argument("experiment")
    s.add_argument("pairs", nargs="*", metavar="NAME=VALUE", help="numbers, e.g. int8.epe=5.64 latency_ms=25.1")
    s.add_argument("--metric", action="append", default=None, help=argparse.SUPPRESS)
    s.add_argument("--variant", default=None, help="the variant these numbers are for: epe=5.64 becomes <variant>.epe")
    s.add_argument("--from", dest="from_file", default=None, metavar="FILE", help="a JSON/YAML/TOML object of metric names to numbers (nested keys join with dots)")
    s.add_argument("--run", default=None, help="an rb review run id, run directory, bundle.json, or results file")
    s.add_argument("--config", default=None, help="the run's own resolved config (Hydra, W&B config.yaml, hparams.yaml): checks it ran with the spec")
    s.add_argument("--set", action="append", default=None, metavar="NAME=VALUE", help="a per-run setting this run used, e.g. seed=2")
    s.add_argument("--commit", default=None, help="the commit that produced the numbers")
    s.add_argument("--artifact", "--file", dest="artifact", action="append", default=None, help="an output whose hash goes into the evidence (repeatable)")
    s.add_argument("--link", action="append", default=None, metavar="NAME=URL", help="where else it lives, e.g. wandb=https://... (repeatable)")
    s.add_argument("--command", dest="command_text", default=None, help="the command that produced the numbers (recorded as given; rb does not run it)")
    s.add_argument("--again", action="store_true", help="attach identical evidence a second time on purpose (with --why)")
    why(s)
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_evidence_attach)
    s = g.add_parser("retract", parents=rc, help=argparse.SUPPRESS)
    s.add_argument("subject")
    s.add_argument("--reason", default=None)
    why(s)
    s.set_defaults(func=cmd_retract)

    s = sub.add_parser("retract", parents=rc, help="take anything back: it stays on record and stops counting (a person's call once something rests on it)")
    s.add_argument("subject", help="an id, or <experiment>/<setting>")
    why(s, required=True)
    s.set_defaults(func=cmd_retract)

    s = sub.add_parser("approve", parents=rc, help="the person's side of a handoff: review what agents asked for, and run or decline it")
    s.add_argument("id", nargs="?", default=None, help="one request (default: every pending one; asked one by one in a terminal)")
    s.add_argument("--decline", action="store_true", help="decline it instead")
    why(s)
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("freeze", parents=rc, help="lock an experiment's spec and claim criteria before the runs that count (a person's call)")
    s.add_argument("experiment")
    s.add_argument("--amend", action="store_true", help="the experiment is frozen: adopt it as it is now (a person's call, with --why)")
    why(s)
    s.set_defaults(func=cmd_freeze)

    s = sub.add_parser("decide", parents=rc, help="a person's call on a claim, hypothesis, assumption, question, experiment, or setting (e1/lr: vouch for it)")
    s.add_argument("subject")
    s.add_argument("outcome", choices=["accept", "reject", "investigate"])
    why(s, required=True)
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("status", parents=rc, help="what is established, what needs a person, what an agent can do")
    s.add_argument("experiment", nargs="?", default=None, help="only this experiment")
    s.add_argument("--fail-on", action="append", default=None, metavar="GATE", help=f"exit 1 when any of: {', '.join(GATES)} (repeatable or comma-separated); an edit made outside rb fails any of them")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("show", parents=rc, help="one object: a claim with its verdict, an experiment with its spec, evidence with its receipt, e1/lr with its history")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("compare", parents=rc, help="an experiment's own table: variants by metrics, with changes against the baseline")
    s.add_argument("experiment", nargs="?", default=None)
    s.set_defaults(func=cmd_compare)

    s = sub.add_parser("log", parents=rc, help="every write, newest last: who, how, what")
    s.add_argument("id", nargs="?", default=None, help="only this object, or <experiment>/<setting>")
    s.add_argument("-n", type=int, default=20, help="how many (default 20)")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("context", parents=rc, help="the handoff pack a fresh agent session reads first")
    s.set_defaults(func=cmd_context)
