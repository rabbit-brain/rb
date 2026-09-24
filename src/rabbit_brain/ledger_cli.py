"""The research-state commands: `rb investigation`, `question`, `hypothesis`, `assumption`, `experiment`, `knob`, `claim`,
`evidence`, `freeze`, `decide`, `status`, `show`, `context`. The rules live in ledger.py; this file parses arguments and
says what happened."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Optional

from .errors import EXIT_CHECK_FAILED, EXIT_OK, RBError
from .investigation import Claim, Experiment, Source
from .ledger import SETTLED, UNSETTLED, Ledger, _show, evidence_from_run
from .runs import resolve_run, runs_dir

GATES = ["unestablished", "unknown", "untested", "refuted", "stale"]
LEDGER_COMMANDS = ["investigation init", "question add", "hypothesis add", "assumption add", "experiment add", "knob set", "knob verify",
                   "claim add", "evidence attach", "evidence retract", "freeze", "decide", "status", "show", "context"]
GROUPS = {"investigation", "question", "hypothesis", "assumption", "experiment", "knob", "claim", "evidence"}


# ---------------------------------------------------------------- parsing helpers


def parse_value(text: str) -> Any:
    """A knob value from the command line: JSON when it parses as a number, boolean, quoted string or list of those
    (so 1e-4 is a number and "6" in quotes is text), otherwise the text itself."""
    try:
        v = json.loads(text)
    except ValueError:
        return text
    def scalar(x: Any) -> bool:
        return isinstance(x, (bool, int, str)) or (isinstance(x, float) and math.isfinite(x))
    if scalar(v) or (isinstance(v, list) and v and all(scalar(x) for x in v)):
        return v
    if v is None:
        raise RBError("E_OBJECT_INVALID", message="null is not a value; record a knob nobody has found with --unknown.")
    raise RBError("E_OBJECT_INVALID", message=f"A knob value is a number, true/false, text, or a list of those; got {text!r}.")


def root_relative(path: str, root: Path) -> str:
    """A path as the user typed it (relative to where they are) becomes relative to the directory holding .rb/, so the
    same knob resolves whichever subdirectory `rb` is run from. A path outside it stays absolute."""
    p = Path(path)
    full = (p if p.is_absolute() else Path.cwd() / p).resolve()
    try:
        return full.relative_to(root).as_posix()
    except ValueError:
        return str(full)


def parse_source(spec: Optional[str], quote: Optional[str], commit: Optional[str], locator: Optional[str], root: Path) -> Optional[Source]:
    """`path`, `path:LINE`, `run:PATH#/json/pointer`, `https://...` or `note:text`, plus --quote, --commit and --locator."""
    if spec is None:
        if quote or commit:
            raise RBError("E_OBJECT_INVALID", message="--quote and --commit describe a --source; give the --source too.")
        return None
    fields: dict[str, Any] = {"locator": locator}
    if spec.startswith("run:"):
        path, sep, pointer = spec[4:].rpartition("#")
        if not sep:
            raise RBError("E_OBJECT_INVALID", message=f"A run source is run:PATH#/json/pointer, e.g. run:rb-runs/<id>/record.json#/seeds/torch (got {spec!r}).")
        fields.update(kind="run", path=root_relative(path, root), pointer=pointer)
    elif spec.startswith(("http://", "https://")):
        fields.update(kind="url", url=spec, quote=quote)
    elif spec.startswith("note:"):
        fields.update(kind="note", note=spec[5:].strip())
    else:
        path, line = spec, None
        head, sep, tail = spec.rpartition(":")
        if sep and tail.isdigit() and head:
            path, line = head, int(tail)
        fields.update(kind="file", path=root_relative(path, root), line=line, quote=quote, commit=commit)
    if fields.get("kind") != "file" and commit:
        raise RBError("E_OBJECT_INVALID", message="--commit applies to a file source.")
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
        if numeric:
            try:
                x = float(val)
            except ValueError:
                raise RBError("E_OBJECT_INVALID", message=f"{what} {key}: {val!r} is not a number.")
            if not math.isfinite(x):
                raise RBError("E_OBJECT_INVALID", message=f"{what} {key} must be finite.")
            out[key] = x
        else:
            out[key] = val
    return out


def metrics_from_file(path: Path) -> tuple[dict[str, float], list[str]]:
    """Numbers in a JSON file, nested keys joined with dots. Everything that is not a number is named, not dropped silently."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise RBError("E_FILE_NOT_FOUND", message=f"No such file: {path}")
    except ValueError:
        raise RBError("E_OBJECT_INVALID", message=f"{path} is not JSON.")
    if not isinstance(doc, dict):
        raise RBError("E_OBJECT_INVALID", message=f"{path} must hold a JSON object of metric names to numbers.")
    numbers: dict[str, float] = {}
    skipped: list[str] = []

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(node, (int, float)) and not isinstance(node, bool) and math.isfinite(node):
            numbers[prefix] = float(node)
        else:
            skipped.append(prefix)
    walk("", doc)
    return numbers, skipped


# ---------------------------------------------------------------- commands


def cmd_investigation_init(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.init(Path.cwd(), args.title, id=args.id)
    inv = led.investigation
    out.say(f"Investigation {inv.id!r} started: {led.dir}", "It is plain files; commit .rb/ with the code so the state travels with it.")
    out.data = {"investigation": inv.model_dump(mode="json"), "dir": str(led.dir)}
    out.next = ['rb question add "<what are you trying to establish?>"', 'rb experiment add "<what would test it>"']
    return EXIT_OK


def cmd_question_add(args: argparse.Namespace, out: Any) -> int:
    q = Ledger.open().add_question(args.text, id=args.id)
    out.say(f"{q.id}: {q.text}")
    out.data = {"question": q.model_dump(mode="json")}
    out.next = [f'rb hypothesis add "<what you expect>" --question {q.id} --expect "..." --why "..."']
    return EXIT_OK


def cmd_hypothesis_add(args: argparse.Namespace, out: Any) -> int:
    h = Ledger.open().add_hypothesis(args.statement, expect=args.expect, why=args.why, question=args.question, id=args.id)
    out.say(f"{h.id} ({h.status}): {h.statement}")
    out.data = {"hypothesis": h.model_dump(mode="json")}
    out.next = [f'rb experiment add "<what would test it>" --tests {h.id}']
    return EXIT_OK


def cmd_assumption_add(args: argparse.Namespace, out: Any) -> int:
    a = Ledger.open().add_assumption(args.text, applies_to=args.applies_to or [], id=args.id)
    out.say(f"{a.id}: {a.text}")
    out.data = {"assumption": a.model_dump(mode="json")}
    return EXIT_OK


def cmd_experiment_add(args: argparse.Namespace, out: Any) -> int:
    e = Ledger.open().add_experiment(args.title, id=args.id, tests=args.tests or [], baseline=args.baseline, candidate=args.candidate, note=args.note)
    out.say(f"Experiment {e.id}: {e.title}" + (f" (tests {', '.join(e.tests)})" if e.tests else ""))
    out.data = {"experiment": e.model_dump(mode="json")}
    out.next = [f"rb knob set {e.id} <name> <value> --source <file:line>", f"rb claim add \"<statement>\" --experiment {e.id} --metric <name> --at-most <x>"]
    return EXIT_OK


def cmd_knob_set(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    if args.value is not None and args.unknown:
        raise RBError("E_OBJECT_INVALID", message="Give a value or --unknown, not both.")
    value = parse_value(args.value) if args.value is not None else None
    source = parse_source(args.source, args.quote, args.commit, args.locator, led.root)
    required = False if args.optional else (True if args.required else None)
    knob = led.set_knob(args.experiment, args.name, value, unknown=args.unknown, source=source, required=required, note=args.note, amend=args.amend)
    out.say(f"{args.experiment}.{knob.name} = {_show(knob.value)} · {knob.status}" + (f" · source {knob.source.label()}" if knob.source else ""))
    rows = []
    if args.verify:
        rows = led.verify_knobs(args.experiment, [knob.name])
        _say_verify(out, rows)
        knob = led.load("experiment", args.experiment).knob(knob.name)
    out.data = {"experiment": args.experiment, "knob": knob.model_dump(mode="json"), "verify": rows}
    if knob.status == "inferred" and knob.source is not None and knob.source.verifiable() and not args.verify:
        out.next = [f"rb knob verify {args.experiment} {knob.name}"]
    elif knob.status == "inferred" and (knob.source is None or not knob.source.verifiable()):
        out.say("It stays inferred until a file or run that states it is given as its source; a url or a note is recorded but cannot be checked.")
    return EXIT_CHECK_FAILED if rows and not all(r["ok"] for r in rows) else EXIT_OK


def _say_verify(out: Any, rows: list[dict]) -> None:
    for r in rows:
        if r["ok"]:
            where = f"line {r['line']}" if r.get("line") else "value"
            at = f" @ {r['commit'][:7]}" if r.get("commit") else " (working tree, uncommitted; a later change to the file will show as stale)"
            out.say(f"  {r['knob']}: verified, {where} reads {r['read']!r}{at}")
        else:
            out.say(f"  {r['knob']}: {r['after']}, not verified ({r['code']}): {r['reason']}")


def cmd_knob_verify(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    rows = led.verify_knobs(args.experiment, args.names or None)
    if not rows:
        out.say(f"{args.experiment} has no knob with a source to verify.")
    _say_verify(out, rows)
    out.data = {"experiment": args.experiment, "results": rows, "verified": sum(1 for r in rows if r["ok"]), "failed": sum(1 for r in rows if not r["ok"])}
    return EXIT_CHECK_FAILED if any(not r["ok"] for r in rows) else EXIT_OK


def cmd_claim_add(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    given = [(c, v) for c, v in (("at_most", args.at_most), ("at_least", args.at_least), ("within", args.within)) if v is not None]
    if len(given) != 1:
        raise RBError("E_OBJECT_INVALID", message="A claim needs exactly one criterion: --at-most X, --at-least X, or --within X --tolerance T.")
    comparator, target = given[0]
    source = parse_source(args.source, args.quote, args.commit, args.locator, led.root)
    claim = led.add_claim(args.statement, metric=args.metric, comparator=comparator, target=target, tolerance=args.tolerance,
                          experiment=args.experiment, hypothesis=args.hypothesis, source=source, note=args.note, amend=args.amend, id=args.id)
    v = led.verdict(claim)
    out.say(f"{claim.id}: {claim.statement}", f"  criterion {claim.criterion()} · {v.status}" + (f" · on {claim.experiment}" if claim.experiment else " · not linked to an experiment yet"))
    out.data = {"claim": claim.model_dump(mode="json"), "verdict": v.model_dump(mode="json")}
    if claim.experiment:
        out.next = [f"rb evidence attach {claim.experiment} --metric {claim.metric}=<value> --command \"<what produced it>\"", f"rb evidence attach {claim.experiment} --run <rb run id>"]
    return EXIT_OK


def cmd_evidence_attach(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    metrics = parse_pairs(args.metric, "--metric", numeric=True)
    files = [root_relative(f, led.root) for f in args.file or []]
    if args.from_file:
        nums, skipped = metrics_from_file(Path(args.from_file))
        overlap = sorted(set(nums) & set(metrics))
        if overlap:
            raise RBError("E_OBJECT_INVALID", message=f"--metric and --from both give {', '.join(overlap)}.")
        metrics.update(nums)
        files.append(root_relative(args.from_file, led.root))
        if skipped:
            out.warn(f"{args.from_file}: not numbers, so not metrics: {', '.join(skipped[:12])}{' ...' if len(skipped) > 12 else ''}")
    run = None
    if args.run:
        bundle, run_dir = resolve_run(args.run, runs_dir(args.runs_dir))
        run = evidence_from_run(bundle, run_dir, led.root)
    ev = led.attach_evidence(args.experiment, metrics=metrics, run=run, files=files, links=parse_pairs(args.link, "--link", numeric=False),
                             command=args.command_text, note=args.note)
    out.say(f"{ev.id} on {ev.experiment}: " + ", ".join(f"{k}={v:g}" for k, v in sorted(ev.metrics.items())))
    if ev.synthetic:
        out.say("  This run is rb's built-in example data; every verdict that uses it will say so.")
    if not ev.receipt.git:
        out.say("  No git repository here, so the receipt has no commit.")
    elif ev.receipt.git.get("dirty"):
        out.say(f"  The tree had uncommitted changes (diff {ev.receipt.git.get('diff_sha256', '')[:12]}); the receipt records that.")
    led_claims = [c for c in led.all("claim") if c.experiment == ev.experiment]
    verdicts = [led.verdict(c) for c in led_claims]
    for v in verdicts:
        out.say(f"  {v.claim} {v.status}: {v.criterion}")
    unused = sorted(set(ev.metrics) - {c.metric for c in led_claims})
    out.data = {"evidence": ev.model_dump(mode="json"), "verdicts": [v.model_dump(mode="json") for v in verdicts], "metrics_no_claim_reads": unused}
    out.next = [f"rb show {v.claim}" for v in verdicts][:3] or [f"rb claim add \"<statement>\" --experiment {ev.experiment} --metric <one of {', '.join(sorted(ev.metrics)[:3])}> --at-most <x>"]
    return EXIT_OK


def cmd_evidence_retract(args: argparse.Namespace, out: Any) -> int:
    ev = Ledger.open().retract_evidence(args.evidence, args.reason)
    out.say(f"{ev.id} retracted: {args.reason}", "It stays on record and no longer counts toward any verdict.")
    out.data = {"evidence": ev.model_dump(mode="json")}
    return EXIT_OK


def cmd_freeze(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    fr = led.freeze(args.experiment)
    exp = led.load("experiment", args.experiment)
    unknown = [k.name for k in exp.knobs if k.status == "unknown" and k.required]
    out.say(f"{exp.id} frozen at {fr.at} (spec {fr.sha256[:12]}). A change to its knobs or claims now needs --amend \"<reason>\", and is kept.")
    if unknown:
        out.say(f"  Frozen with required knobs unknown: {', '.join(unknown)}. Every verdict on it will say so.")
    out.data = {"experiment": exp.id, "frozen": fr.model_dump(mode="json"), "unknown_required": unknown}
    return EXIT_OK


def cmd_decide(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    d = led.decide(args.subject, args.outcome, args.why)
    against = f" (verdict at the time: {d.verdict})" if d.verdict else ""
    out.say(f"{d.id}: {d.outcome} {d.subject} by {d.by}{against}: {d.why}")
    if d.verdict and ((d.outcome == "accept" and d.verdict not in SETTLED) or (d.outcome == "reject" and d.verdict in SETTLED)):
        out.say(f"  This decision goes against the verdict ({d.verdict}). It is recorded as made; `rb status` shows both.")
    out.data = {"decision": d.model_dump(mode="json")}
    return EXIT_OK


def _verdict_line(v: dict) -> str:
    obs = v.get("observations") or []
    seen = "; ".join(f"{o['value']:g} ({o['evidence']})" for o in obs[:3]) or "no evidence"
    standing = v["status"] + ("" if v.get("established") or v["status"] not in SETTLED else ", not established")
    line = f"  {v['claim']:<5} {standing:<11} {v['statement']}\n        {v['criterion']} · observed {seen}"
    if v.get("blocking"):
        line += f"\n        blocked on unknown: {', '.join(v['blocking'])}"
    if v.get("conditions"):
        line += "\n        rests on: " + "; ".join(v["conditions"][:4]) + (" ..." if len(v["conditions"]) > 4 else "")
    if v.get("decision"):
        line += f"\n        decided: {v['decision']}"
    return line


def _verdict_bullet(v: dict) -> str:
    """One claim for the context pack: the verdict, then everything it rests on, indented under it."""
    return "- " + _verdict_line(v).strip().replace("\n        ", "\n  ")


def _knob_counts(e: dict) -> str:
    k = e["knobs"]
    s = f"knobs {k['verified']} verified · {k['inferred']} inferred · {k['unknown']} unknown"
    if k.get("imported"):
        s += f" · {k['imported']} imported"
    if e["blocking"]:
        s += f" ({', '.join(e['blocking'])} required)"
    return s


def cmd_status(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    st = led.status()
    inv, c = st["investigation"], st["counts"]
    out.say(f"{inv['title']} ({inv['id']}) · {st['root']}",
            f"{c['questions']} questions · {c['hypotheses']} hypotheses · {c['experiments']} experiments · {c['claims']} claims · {c['evidence']} evidence · {c['decisions']} decisions")
    if st["claims"]:
        out.say("", "Claims")
        out.say(*[_verdict_line(v) for v in st["claims"]])
    if st["experiments"]:
        out.say("", "Experiments")
        for e in st["experiments"]:
            frozen = f"frozen {e['frozen']['sha256'][:12]}" + (f", amended {e['amendments']}x" if e["amendments"] else "") if e["frozen"] else "not frozen"
            out.say(f"  {e['id']:<5} {e['title']} · {frozen} · {_knob_counts(e)} · evidence {e['evidence']}" + (f" ({e['retracted']} retracted)" if e["retracted"] else ""))
    out.say("", f"Open ({len(st['open'])})" if st["open"] else "Nothing open.")
    out.say(*[f"  - {o['what']}" for o in st["open"]])
    fail_on = [g for item in (args.fail_on or []) for g in item.split(",") if g]
    bad = [g for g in fail_on if g not in GATES]
    if bad:
        raise RBError("E_OBJECT_INVALID", message=f"--fail-on takes {', '.join(GATES)} (got {', '.join(bad)}).")
    tripped = [g for g in fail_on if st["gate"][g]]
    st["fail_on"], st["failed"] = fail_on, bool(tripped)
    if tripped:
        out.say("", f"GATE FAILED on: {', '.join(tripped)}.")
    out.data = st
    out.next = ["rb context"] if st["open"] else []
    return EXIT_CHECK_FAILED if tripped else EXIT_OK


def cmd_show(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    kind, obj = led.get(args.id)
    data: dict[str, Any] = {"kind": kind, kind: obj.model_dump(mode="json")}
    if kind == "claim":
        v = led.verdict(obj)
        data["verdict"] = v.model_dump(mode="json")
        out.say(f"Claim {obj.id} ({obj.origin}): {obj.statement}" + (f"\n  source: {obj.source.label()}" if obj.source else ""))
        out.say(_verdict_line(data["verdict"]))
        for o in v.observations:
            out.say(f"  {o.evidence}: {obj.metric} = {o.value:g} · {'holds' if o.holds else 'misses'} by {abs(o.margin):g}" + (" · borderline" if o.borderline else "") + f" · spec {o.spec.replace('_', ' ')}")
        if v.without_metric:
            out.say(f"  Evidence on {obj.experiment} that does not report {obj.metric}: {', '.join(v.without_metric)}")
        if v.retracted:
            out.say(f"  Retracted, not counted: {', '.join(v.retracted)}")
    elif kind == "experiment":
        _show_experiment(led, obj, out, data)
    elif kind == "evidence":
        out.say(f"Evidence {obj.id} on {obj.experiment} ({obj.kind}) · attached {obj.receipt.at} by {obj.receipt.actor}")
        out.say(*[f"  {k} = {v:g}" for k, v in sorted(obj.metrics.items())])
        if obj.receipt.command:
            out.say(f"  command (as given): {obj.receipt.command}")
        if obj.receipt.git:
            g = obj.receipt.git
            out.say(f"  git {g['commit'][:12]}{' with uncommitted changes' if g.get('dirty') else ''}")
        if obj.retracted:
            out.say(f"  RETRACTED {obj.retracted.at} by {obj.retracted.by}: {obj.retracted.reason}")
    else:
        text = getattr(obj, "text", None) or getattr(obj, "statement", None) or getattr(obj, "why", "")
        status = getattr(obj, "status", None) or getattr(obj, "outcome", "")
        out.say(f"{kind.capitalize()} {obj.id} ({status}): {text}")
    decisions = led.decisions_on(obj.id)
    if decisions:
        data["decisions"] = [d.model_dump(mode="json") for d in decisions]
        out.say(*[f"  decision {d.id}: {d.outcome} by {d.by} at {d.at}: {d.why}" for d in decisions])
    out.data = data
    return EXIT_OK


def _show_experiment(led: Ledger, exp: Experiment, out: Any, data: dict) -> None:
    out.say(f"Experiment {exp.id}: {exp.title}")
    if exp.tests:
        out.say(f"  tests: {', '.join(exp.tests)}")
    if exp.baseline or exp.candidate:
        out.say(f"  baseline: {exp.baseline or '(not set)'} · candidate: {exp.candidate or '(not set)'}")
    out.say(f"  spec {led.spec_sha(exp)[:12]} · " + (f"frozen {exp.frozen.at} by {exp.frozen.by} ({exp.frozen.sha256[:12]})" if exp.frozen else "not frozen"))
    for a in exp.amendments:
        out.say(f"  amended {a.at} by {a.by}: {a.change} · because: {a.reason}")
    blocking, conditions, stale = led.knob_conditions(exp)
    if exp.knobs:
        out.say("  knobs:")
        for k in exp.knobs:
            src = f" · {k.source.label()}" if k.source else ""
            read = f" · read {k.source.resolved.text!r}" if k.source and k.source.resolved else ""
            flag = " · STALE" if k.name in stale else ""
            out.say(f"    {k.name:<20} {_show(k.value):<14} {k.status}{'' if k.required else ' (optional)'}{src}{read}{flag}")
    claims = [led.verdict(c) for c in led.claims_of(exp.id)]
    if claims:
        out.say("  claims:")
        out.say(*[_verdict_line(v.model_dump(mode="json")) for v in claims])
    evidence = [e for e in led.all("evidence") if e.experiment == exp.id]
    if evidence:
        out.say("  evidence: " + ", ".join(f"{e.id}{' (retracted)' if e.retracted else ''}" for e in evidence))
    data.update({"spec_sha256": led.spec_sha(exp), "blocking": blocking, "conditions": conditions, "stale": stale,
                 "claims": [v.model_dump(mode="json") for v in claims], "evidence": [e.id for e in evidence]})


def context_markdown(led: Ledger, st: dict) -> str:
    """The handoff pack: what a fresh agent session (or a colleague) reads first instead of anyone's summary."""
    inv = st["investigation"]
    lines = [f"# Research state: {inv['title']}", "",
             "Generated by `rb context` from `.rb/`. Read the state from here or `rb status --json`; do not rebuild it from memory or a summary.", "",
             "## Rules", "",
             "- Propose, do not decide. You may add questions, hypotheses, assumptions, experiments, knob values with their sources, claims and evidence.",
             "- Only `rb knob verify` makes a knob verified. Only `rb` computes a claim's verdict. Never edit `.rb/` by hand.",
             "- Freezing, deciding and retracting are a person's calls. Set `RB_ACTOR=agent:<your name>` so the record says who did what.",
             "- Pass on every condition and every unknown below with any result you report.", ""]
    settled = [v for v in st["claims"] if v["established"]]
    unsettled = [v for v in st["claims"] if not v["established"]]
    lines += ["## Established", ""] + ([_verdict_bullet(v) for v in settled] or ["- Nothing yet."]) + [""]
    lines += ["## Not established", ""] + ([_verdict_bullet(v) for v in unsettled] or ["- Nothing."]) + [""]
    lines += ["## Open: work on these", ""] + ([f"- {o['what']}" for o in st["open"]] or ["- Nothing open."]) + [""]
    if st["experiments"]:
        lines += ["## Experiments", ""]
        for e in st["experiments"]:
            frozen = f"frozen {e['frozen']['sha256'][:12]}" if e["frozen"] else "not frozen"
            lines.append(f"- {e['id']} {e['title']}: {frozen}; {_knob_counts(e)}; evidence {e['evidence']}")
        lines.append("")
    if st["hypotheses"]:
        lines += ["## Hypotheses", ""] + [f"- {h['id']} ({h['status']}): {h['statement']}" + (f" Expect: {h['expect']}" if h["expect"] else "") for h in st["hypotheses"]] + [""]
    if st["decisions"]:
        lines += ["## Decisions", ""] + [f"- {d['id']}: {d['outcome']} {d['subject']} by {d['by']}" + (f" (verdict then: {d['verdict']})" if d.get("verdict") else "") + f": {d['why']}" for d in st["decisions"]] + [""]
    recent = led.log_entries(last=10)
    if recent:
        lines += ["## Recent activity", ""] + [f"- {r['at']} {r['actor']} {r['op']} {r['kind']} {r['id']}" for r in recent] + [""]
    return "\n".join(lines)


def cmd_context(args: argparse.Namespace, out: Any) -> int:
    led = Ledger.open()
    st = led.status()
    md = context_markdown(led, st)
    out.say(md.rstrip("\n"))
    out.data = {"markdown": md, "state": st}
    return EXIT_OK


# ---------------------------------------------------------------- parsers


def add_parsers(sub: Any, common: argparse.ArgumentParser) -> None:
    def group(name: str, help: str) -> Any:
        g = sub.add_parser(name, help=help)
        return g.add_subparsers(dest="sub_command", metavar="<" + help.split(":")[0] + ">", required=True)

    def src(s: argparse.ArgumentParser, what: str) -> None:
        s.add_argument("--source", default=None, help=f"where {what} comes from: path, path:LINE, run:PATH#/json/pointer, https://..., or note:text")
        s.add_argument("--quote", default=None, help="the exact text in the source that states it (checked by rb knob verify)")
        s.add_argument("--commit", default=None, help="read the file at this git commit instead of the working tree")
        s.add_argument("--locator", default=None, help="how a reader finds it, e.g. \"§4.2\" or \"Table 3, row 4\"")

    g = group("investigation", "init: start the research state (.rb/) in this directory")
    s = g.add_parser("init", parents=[common], help="create .rb/ here")
    s.add_argument("title")
    s.add_argument("--id", default=None, help="a short id (default: from the title)")
    s.set_defaults(func=cmd_investigation_init)

    g = group("question", "add: a question the research is trying to answer")
    s = g.add_parser("add", parents=[common], help="add a question")
    s.add_argument("text")
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_question_add)

    g = group("hypothesis", "add: what you expect, and why")
    s = g.add_parser("add", parents=[common], help="add a hypothesis")
    s.add_argument("statement")
    s.add_argument("--expect", default="", help="the result you expect")
    s.add_argument("--why", default="", help="the reasoning")
    s.add_argument("--question", default=None, help="the question it answers")
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_hypothesis_add)

    g = group("assumption", "add: something the work takes for granted")
    s = g.add_parser("add", parents=[common], help="add an assumption")
    s.add_argument("text")
    s.add_argument("--applies-to", action="append", default=None, help="experiment id (repeatable)")
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_assumption_add)

    g = group("experiment", "add: what would test a hypothesis")
    s = g.add_parser("add", parents=[common], help="add an experiment")
    s.add_argument("title")
    s.add_argument("--id", default=None, help="a short id (default e1, e2, ...)")
    s.add_argument("--tests", action="append", default=None, help="hypothesis id it tests (repeatable)")
    s.add_argument("--baseline", default=None, help="what it compares against: a run id, a checkpoint, an hf:// reference")
    s.add_argument("--candidate", default=None, help="what is being tested")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_experiment_add)

    g = group("knob", "set|verify: an experiment's settings, their sources and statuses")
    s = g.add_parser("set", parents=[common], help="propose a knob's value with its source (inferred), or record it as unknown")
    s.add_argument("experiment")
    s.add_argument("name")
    s.add_argument("value", nargs="?", default=None, help="a number, true/false, text, or a JSON list; omit with --unknown")
    s.add_argument("--unknown", action="store_true", help="nobody has found this value yet")
    src(s, "the value")
    req = s.add_mutually_exclusive_group()
    req.add_argument("--optional", action="store_true", help="an unknown value does not block the experiment's claims")
    req.add_argument("--required", action="store_true", help="an unknown value blocks (the default for a new knob)")
    s.add_argument("--note", default=None)
    s.add_argument("--verify", action="store_true", help="also run rb knob verify on it")
    s.add_argument("--amend", default=None, metavar="REASON", help="the experiment is frozen: change it anyway, recording why")
    s.set_defaults(func=cmd_knob_set)
    s = g.add_parser("verify", parents=[common], help="read each knob's source and mark it verified only if the source states the value")
    s.add_argument("experiment")
    s.add_argument("names", nargs="*", help="knob names (default: every knob with a source)")
    s.set_defaults(func=cmd_knob_verify)

    g = group("claim", "add: a statement with a criterion evidence can meet or miss")
    s = g.add_parser("add", parents=[common], help="add a claim")
    s.add_argument("statement")
    s.add_argument("--metric", required=True, help="the evidence metric it reads, e.g. candidate.mean_endpoint_error or latency_ms")
    s.add_argument("--at-most", type=float, default=None)
    s.add_argument("--at-least", type=float, default=None)
    s.add_argument("--within", type=float, default=None, metavar="TARGET", help="with --tolerance: reproduced when |observed - TARGET| <= tolerance")
    s.add_argument("--tolerance", type=float, default=None)
    s.add_argument("--experiment", default=None, help="the experiment whose evidence tests it")
    s.add_argument("--hypothesis", default=None)
    src(s, "the claimed number (a paper's table, someone else's result)")
    s.add_argument("--note", default="")
    s.add_argument("--amend", default=None, metavar="REASON", help="the experiment is frozen: add it anyway, recording why")
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_claim_add)

    g = group("evidence", "attach|retract: numbers from a run, with a receipt")
    s = g.add_parser("attach", parents=[common], help="attach numbers from an rb run or from any evaluator to an experiment")
    s.add_argument("experiment")
    s.add_argument("--run", default=None, help="an rb run id, run directory or bundle.json")
    s.add_argument("--metric", action="append", default=None, metavar="NAME=VALUE", help="a number from any evaluator (repeatable)")
    s.add_argument("--from", dest="from_file", default=None, metavar="FILE", help="a JSON object of metric names to numbers (nested keys join with dots)")
    s.add_argument("--file", action="append", default=None, help="an output file to hash into the receipt (repeatable)")
    s.add_argument("--link", action="append", default=None, metavar="NAME=URL", help="where else it lives, e.g. wandb=https://... (repeatable)")
    s.add_argument("--command", dest="command_text", default=None, help="the command that produced the numbers (recorded as given; rb does not run it)")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_evidence_attach)
    s = g.add_parser("retract", parents=[common], help="stop evidence counting, keeping it on record with the reason (a person's call)")
    s.add_argument("evidence")
    s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_evidence_retract)

    s = sub.add_parser("freeze", parents=[common], help="lock an experiment's spec before looking at held-out results (a person's call)")
    s.add_argument("experiment")
    s.set_defaults(func=cmd_freeze)

    s = sub.add_parser("decide", parents=[common], help="record a person's decision on a claim, hypothesis, assumption, question or experiment")
    s.add_argument("subject")
    s.add_argument("outcome", choices=["accept", "reject", "investigate"])
    s.add_argument("--why", required=True)
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("status", parents=[common], help="what is established, what is not, and what is open")
    s.add_argument("--fail-on", action="append", default=None, metavar="GATE", help=f"exit 1 when any of: {', '.join(GATES)} (repeatable or comma-separated)")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("show", parents=[common], help="one object: a claim with its verdict, an experiment with its knobs, evidence with its receipt")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("context", parents=[common], help="the handoff pack a fresh agent session reads first")
    s.set_defaults(func=cmd_context)
