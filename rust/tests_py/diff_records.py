"""Differential test: record validation and the stored form, Rust against pydantic and record.stored(), on every record
file of a corpus (the test suite's own .rb/ directories) and on random mutations of them."""
import copy, glob, json, random, subprocess, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests"))      # rbyaml: the reference reader
from pydantic import ValidationError
import rbyaml
from rabbit_brain import record

BIN, CORPUS = sys.argv[1], sys.argv[2]
N_MUT = int(sys.argv[3]) if len(sys.argv) > 3 else 5
random.seed(3)
MODELS = {"project": "Project", "question": "Question", "experiment": "Experiment", "metric": "Metric", "run": "Run", "analysis": "Analysis",
          "resource": "Resource", "decision": "Decision"}

items = []
for path in glob.glob(os.path.join(CORPUS, "**", ".rb", "**", "*.yaml"), recursive=True):
    try:
        data = rbyaml.loads(open(path, encoding="utf-8").read())
    except Exception:
        continue
    if not isinstance(data, dict):
        continue
    kind = data.get("kind")
    body = {k: v for k, v in data.items() if k not in ("rb", "kind")}
    if kind == "criteria":
        for c in body.get("criteria") or []:
            if isinstance(c, dict):
                items.append(("Criterion", {**c, "experiment": body.get("experiment", "e")}))
    elif kind in MODELS:
        items.append((MODELS[kind], body))

def leaves(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,), v
            yield from leaves(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield path + (i,), v
            yield from leaves(v, path + (i,))

def mutate(d):
    d = copy.deepcopy(d)
    spots = list(leaves(d))
    if not spots:
        return d
    path, _ = random.choice(spots)
    parent = d
    for step in path[:-1]:
        parent = parent[step]
    last = path[-1]
    r = random.random()
    choices = [None, 0, 1, 2, -1, 1.5, float("1e300"), "", " ", "x", "yes", "human:x", "agent:y", "verified", "unknown", [], [1], ["a", "a"], {}, {"a": 1}, True, False, "1.5", "3", "nope nope"]
    if r < 0.6:
        parent[last] = random.choice(choices)
    elif r < 0.8 and isinstance(parent, dict):
        del parent[last]
    elif isinstance(parent, dict):
        parent["zz_extra"] = 1
    else:
        parent.append(random.choice(choices))
    return d

cases = list(items)
for model, body in items:
    for _ in range(N_MUT):
        cases.append((model, mutate(body)))

def py(model, data):
    m = getattr(record, model)
    try:
        obj = m.model_validate(data)
    except ValidationError as e:
        return [[".".join(str(p) for p in x["loc"]), x["type"], x["msg"]] for x in e.errors()]
    return {"stored": json.dumps(record.stored(obj))}

reqs, expect = [], []
for model, data in cases:
    try:
        text = json.dumps(data, allow_nan=True)
    except Exception:
        continue
    reqs.append({"op": "validate", "model": model, "data": text})
    expect.append(py(model, data))
r = subprocess.run([BIN], input="\n".join(json.dumps(q) for q in reqs), capture_output=True, text=True)
got = [json.loads(l) for l in r.stdout.splitlines()]
assert len(got) == len(expect), (len(got), len(expect), r.stderr[-3000:])
bad = [(q, e, g) for q, e, g in zip(reqs, expect, got) if e != g]
for q, e, g in bad[:6]:
    print("MISMATCH", q["model"], q["data"][:400])
    print("  python:", json.dumps(e)[:600])
    print("  rust:  ", json.dumps(g)[:600])
print(f"{len(reqs) - len(bad)} of {len(reqs)} agree ({len(items)} corpus records)")
sys.exit(1 if bad else 0)
