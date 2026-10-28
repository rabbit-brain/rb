"""Differential test: the Rust rbyaml against the Python one, on every YAML file in the repository, generated
documents, and random mutations of both. Data, comments, canonical form and error messages must all agree."""
import glob, json, random, subprocess, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests"))      # rbyaml: the reference reader
import rbyaml

BIN = sys.argv[1]
N_MUT = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
random.seed(11)

def step(s):
    if isinstance(s, str): return ["k", s]
    if isinstance(s, int): return ["i", s]
    return ["n", s[0], s[1]]

def py(text):
    try:
        d = rbyaml.load(text, "t.yaml")
    except rbyaml.YAMLError as e:
        return "ERROR " + str(e)
    c = d.comments
    try:
        fmt = rbyaml.dumps(d.data, d.comments)
    except (ValueError, TypeError) as e:
        fmt = "ERROR " + str(e)
    return {"data": json.dumps(d.data), "comments": {"before": [[[step(s) for s in p], v] for p, v in c.before.items()],
                                                       "after": [[[step(s) for s in p], v] for p, v in c.after.items()],
                                                       "footer": c.footer}, "fmt": fmt}

root = os.path.join(os.path.dirname(__file__), "..", "..")
texts = []
for path in glob.glob(os.path.join(root, "**", "*.yaml"), recursive=True) + glob.glob(os.path.join(root, "**", "*.yml"), recursive=True):
    if "/target/" in path or "node_modules" in path: continue
    try: texts.append(open(path, encoding="utf-8").read())
    except Exception: pass
snippets = [
    "a: 1\n", "a: yes\n", "a: 08\n", "a: 1e3\n", "a: .inf\n", "a: 1.0e+999\n", "a: [1.0, -9.9e+400]\n", "a: 1.7e+308\n", "a: 2026-09-30\n", "a: 10:30\n", "a: 'it''s'\n", 'a: "x\\ty\\u00e9"\n',
    "a: |\n  line1\n  line2\n", "a: >-\n  folded\n  text\n\n  para\n", "a: [1, 2, {b: c}]\n", "a: {x: 1, y: [true, null]}\n",
    "- a\n- b: 1\n  c: 2\n- - x\n  - y\n", "---\na: 1\n...\n", "a: &x 1\n", "a: *x\n", "a: !!str 1\n", "? a\n: b\n", "a:\n\tb: 1\n",
    "a: b: c\n", "a: 'unclosed\n", "a: \"multi\n  line\"\n", "a: plain\n  continued\n  # not a comment? \n", "a: x # c\nb: y\n",
    "# only a comment\n", "", "\n\n", "a:\n  - 1\n  -\n  - 3\n", "a: -1\nb: +2\nc: -0.5\nd: 1.0e+3\ne: 1_000\n", "key with spaces: v\n",
    "\"quoted key\": 1\n", "a: [1,\n  2,\n  3]\n", "a: {x: 1,\n  y: 2}\n", "a: ~\nb: null\nc: Null\n", "a: 'x'\n  # c\n", "a:\n  b:\n    c:\n      d: 1\n",
]
texts += snippets

def gen_value(depth=0):
    r = random.random()
    if depth > 3 or r < 0.5:
        return random.choice([None, True, False, 0, -1, 7, 10**20, 0.5, -2.25, 1e-05, 1e16, 3.0, "", "x", "a b", "yes", "08", "1e3",
                              "2026-09-30", "a: b", "#hash", " lead", "trail ", "multi\nline\n", "line\nnoend", "\n\nlead", "tab\there",
                              "it's", 'say "hi"', "é unicode", "---", "...", "- dash", "[x]", "{y}", "@at", "`tick", "%pct", "x:", "<<", "="])
    if r < 0.75:
        return [gen_value(depth + 1) for _ in range(random.randint(0, 4))]
    keys = ["id", "name", "a", "b", "kind", "rb", "x y", "1", "true", "é", "key: colon", "-k"]
    d = {}
    for _ in range(random.randint(0, 4)):
        d[random.choice(keys)] = gen_value(depth + 1)
    if random.random() < 0.3:
        return [{"id": f"i{n}", "v": gen_value(depth + 2)} for n in range(random.randint(1, 3))]
    return d

gen = []
for _ in range(3000):
    v = gen_value()
    try:
        gen.append(rbyaml.dumps(v))
    except Exception:
        pass
texts += gen

ALPHA = list(" \t\n-:#'\"[]{},|>!&*?%@`~.0123456789abcxyz\\") + ["\u00e9", "\u2028"]
mut = []
base = [t for t in texts if t]
for _ in range(N_MUT):
    t = list(random.choice(base))
    for _ in range(random.randint(1, 3)):
        if not t: break
        k = random.randrange(len(t))
        op = random.random()
        if op < 0.4: t[k] = random.choice(ALPHA)
        elif op < 0.7: t.insert(k, random.choice(ALPHA))
        else: del t[k]
    mut.append("".join(t))
texts += mut

reqs = [{"op": "yaml_load", "text": t, "source": "t.yaml"} for t in texts]
dump_vals = []
for _ in range(3000):
    v = gen_value()
    dump_vals.append(v)
reqs += [{"op": "yaml_dumps", "data": json.dumps(v)} for v in dump_vals]
expect = [py(t) for t in texts]
for v in dump_vals:
    try: expect.append(rbyaml.dumps(v))
    except (ValueError, TypeError) as e: expect.append("ERROR " + str(e))
r = subprocess.run([BIN], input="\n".join(json.dumps(q) for q in reqs), capture_output=True, text=True)
got = [json.loads(l) for l in r.stdout.splitlines()]
assert len(got) == len(expect), (len(got), len(expect), r.stderr[-2000:])
bad = [(q, e, g) for q, e, g in zip(reqs, expect, got) if e != g]
for q, e, g in bad[:8]:
    print("MISMATCH", json.dumps(q)[:300])
    print("  python:", json.dumps(e)[:400])
    print("  rust:  ", json.dumps(g)[:400])
print(f"{len(reqs) - len(bad)} of {len(reqs)} agree")
sys.exit(1 if bad else 0)
