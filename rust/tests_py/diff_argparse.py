"""Differential test: rb's command line parsed by the Rust argparse against Python's argparse itself, on the command
line as it is declared (rust/src/cli_parser.py): the namespace, every usage error (message and usage text), and every
--help text, for generated command lines over every parser."""
import argparse, contextlib, io, json, os, random, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
os.environ["COLUMNS"] = os.environ.get("COLUMNS", "100")
from cli_parser import build_parser, UsageError

BIN = sys.argv[1]
random.seed(5)
# --version says the binary's own version, which the declarations leave open
VERSION = re.search(r'pub const VERSION: &str = "([^"]+)";', open(os.path.join(HERE, "..", "src", "lib.rs"), encoding="utf-8").read()).group(1)
parser = build_parser(os.environ.get("RB_TEST_VERSION") or VERSION)

def py(argv):
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            ns = parser.parse_args(argv)
    except UsageError as e:
        return {"usage_error": e.message, "usage": e.usage}
    except SystemExit as e:
        return {"exit": e.code if isinstance(e.code, int) else 2, "out": out.getvalue()}
    d = {}
    for k, v in vars(ns).items():
        d[k] = v.__name__ if callable(v) else v
    return {"ns": d}

def leaves(p, path):
    subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
    yield path, p
    for a in subs:
        for name, sp in a.choices.items():
            yield from leaves(sp, path + [name])

def sample(a, rnd):
    if a.choices is not None:
        return str(rnd.choice(list(a.choices)))
    if a.type is float:
        return rnd.choice(["-1e-3", "0.5", "2", "-0.001", "1_000.5", "x", "nan", "inf"])
    if a.type is int:
        return rnd.choice(["2", "-3", "07", "x", "1.5", " 4 "])
    return rnd.choice(["x", "a b", "-x", "--", "e1", "e1/lr", "1e-4", "-5", "é", ""])

cases = [[], ["--version"], ["nope"], ["-x"], ["--json"]]
for path, p in leaves(parser, []):
    cases.append(path + ["--help"])
    cases.append(path + ["-h"])
    cases.append(path + ["--bogus"])
    pos = [a for a in p._actions if not a.option_strings and not isinstance(a, argparse._SubParsersAction)]
    opts = [a for a in p._actions if a.option_strings and not isinstance(a, (argparse._HelpAction, argparse._VersionAction))]
    for trial in range(12):
        rnd = random.Random(hash((tuple(path), trial)))
        argv = list(path)
        for a in pos:
            n = {None: 1, "?": rnd.choice([0, 1]), "*": rnd.randint(0, 3), "+": rnd.randint(0, 2), "...": rnd.randint(0, 3)}.get(a.nargs, 1)
            if rnd.random() < 0.1: n = 0
            argv += [sample(a, rnd) for _ in range(n)]
        for a in rnd.sample(opts, min(len(opts), rnd.randint(0, 4))):
            o = rnd.choice(a.option_strings)
            if rnd.random() < 0.3 and o.startswith("--") and len(o) > 4:
                o = o[:rnd.randint(3, len(o))]
            if a.nargs == 0:
                argv.append(o)
            elif rnd.random() < 0.3 and o.startswith("--"):
                argv.append(f"{o}={sample(a, rnd)}")
            elif a.nargs in ("*", "+"):
                argv.append(o); argv += [sample(a, rnd) for _ in range(rnd.randint(0, 2))]
            else:
                argv += [o, sample(a, rnd)]
        if rnd.random() < 0.2:
            argv.insert(rnd.randint(len(path), len(argv)), "--")
        if rnd.random() < 0.1:
            argv.append("--json")
        rnd.shuffle(argv[len(path):]) if rnd.random() < 0.2 else None
        cases.append(argv)

reqs = [{"op": "argv", "argv": c} for c in cases]
expect = [py(c) for c in cases]
env = dict(os.environ)
r = subprocess.run([BIN], input="\n".join(json.dumps(q) for q in reqs), capture_output=True, text=True, env=env)
got = [json.loads(l) for l in r.stdout.splitlines()]
assert len(got) == len(expect), (len(got), len(expect), r.stderr[-3000:])
def norm(x):
    if "ns" in x:
        return {"ns": json.loads(json.dumps(x["ns"]))}
    return x
bad = [(q, e, g) for q, e, g in zip(reqs, expect, got) if norm(e) != norm(g)]
for q, e, g in bad[:6]:
    print("MISMATCH", q["argv"])
    print("  python:", json.dumps(e)[:900])
    print("  rust:  ", json.dumps(g)[:900])
print(f"{len(reqs) - len(bad)} of {len(reqs)} agree")
sys.exit(1 if bad else 0)
