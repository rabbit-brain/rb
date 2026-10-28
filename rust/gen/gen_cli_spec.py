"""Write rb's command line (rust/src/cli_parser.py, an argparse tree) to rust/src/cli_spec.json beside it: every
parser, action, group and default, in order. The binary builds the same tree from it (rust/src/argparse.rs), so every
command line parses as Python's argparse parses it. Run it after changing cli_parser.py: python rust/gen/gen_cli_spec.py"""
import argparse, json, os, sys
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)
from cli_parser import build_parser

SPEC = os.path.join(SRC, "cli_spec.json")

KIND = {argparse._StoreAction: "store", argparse._StoreTrueAction: "store_true", argparse._StoreFalseAction: "store_false",
        argparse._StoreConstAction: "store_const", argparse._AppendAction: "append", argparse._AppendConstAction: "append_const",
        argparse._CountAction: "count", argparse._HelpAction: "help", argparse._VersionAction: "version",
        argparse._SubParsersAction: "parsers"}


def val(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [val(x) for x in v]
    if callable(v):
        return {"callable": v.__name__}
    raise TypeError(repr(v))


def required(a):
    """Whether the argument must be given, by argparse's rule of 3.10 and 3.11: 3.12.7 and 3.13.1 stopped marking a
    positional taking '*' or '...' as required (gh-72795), and the spec is the same whichever Python writes it."""
    if a.option_strings or isinstance(a, argparse._SubParsersAction):
        return a.required
    return a.nargs not in ("?", "*") or (a.nargs == "*" and a.default is None)


def action(a, parser):
    d = {"kind": KIND[type(a)]}
    if a.option_strings:
        d["options"] = list(a.option_strings)
    d["dest"] = a.dest
    if a.nargs is not None:
        d["nargs"] = a.nargs
    if a.const is not None:
        d["const"] = val(a.const)
    if a.default is not None:
        d["default"] = val(a.default)
    if a.type is not None:
        d["type"] = a.type.__name__
    if a.choices is not None and not isinstance(a, argparse._SubParsersAction):
        d["choices"] = [val(c) for c in a.choices]
    if required(a):
        d["required"] = True
    if a.help is not None:
        d["help"] = a.help
    if a.metavar is not None:
        d["metavar"] = val(a.metavar)
    if isinstance(a, argparse._VersionAction):
        d["version"] = a.version
    if isinstance(a, argparse._SubParsersAction):
        d["parsers"] = [[name, dump(sp)] for name, sp in a.choices.items()]
        d["choice_actions"] = [{"dest": c.dest, "metavar": c.metavar, **({"help": c.help} if c.help is not None else {})} for c in a._choices_actions]
    return d


def dump(p):
    acts = list(p._actions)
    index = {id(a): n for n, a in enumerate(acts)}
    out = {"prog": p.prog}
    if p.usage is not None:
        out["usage"] = p.usage
    if p.description is not None:
        out["description"] = p.description
    if p.epilog is not None:
        out["epilog"] = p.epilog
    if not p.add_help:
        out["add_help"] = False
    assert p.prefix_chars == "-" and p.allow_abbrev and p.fromfile_prefix_chars is None and p.argument_default is None
    out["actions"] = [action(a, p) for a in acts]
    out["groups"] = [{"title": g.title, **({"description": g.description} if g.description else {}), "actions": [index[id(a)] for a in g._group_actions]}
                     for g in p._action_groups]
    if p._mutually_exclusive_groups:
        out["mutex"] = [{"required": bool(m.required), "actions": [index[id(a)] for a in m._group_actions]} for m in p._mutually_exclusive_groups]
    defaults = {k: (v if k == "func" else val(v)) for k, v in p._defaults.items()}    # a func is the name of the binary's function
    if defaults:
        out["defaults"] = defaults
    return out


def text() -> str:
    """The spec file's whole text, as the declarations give it now."""
    return json.dumps(dump(build_parser()), indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    with open(SPEC, "w", encoding="utf-8") as f:
        f.write(text())
    print("ok")
