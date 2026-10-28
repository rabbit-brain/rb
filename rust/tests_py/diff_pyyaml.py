"""Differential test: reading a project's YAML configs, Rust against PyYAML with rb's loader, and the line/key-path
mapping, as the reference implementation has them (reference.py: _yaml_loader, yaml_line_of, yaml_key_at)."""
import json, random, subprocess, sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml
import reference as sources

BIN = sys.argv[1]
random.seed(9)
texts = [
    "model: resnet50\noptim:\n  lr: 1e-4\n  betas: [0.9, 0.999]\nbatch_size: 6\namp: false\nlog_every: 50\n",
    "a: yes\nb: no\nc: on\nd: off\ne: 010\nf: 0x1F\ng: 1_000\nh: 1:30\ni: .inf\nj: ~\nk: 2026-09-30\nl: 1e-4\nm: 1.0e+3\nn: -.5\no: 0b101\np: 0\nq: 12:30:00\nr: +3\ns: 1.5e3\n",
    "base: &b\n  lr: 0.1\n  wd: 0.01\nexp:\n  <<: *b\n  lr: 0.2\n",
    "list:\n  - 1\n  - two\n  - [3, 4]\n  - {five: 5}\nnested:\n  deep:\n    deeper: 'quoted'\n    lit: |\n      a\n      b\n",
    "_wandb:\n  desc: null\n  value: {cli_version: 0.15}\nlr:\n  desc: null\n  value: 0.001\n",
    "1: one\ntrue: t\nnull: n\n3.5: f\n",
    "key with space: v\n'quoted key': 2\n\"dq\": 3\n",
    "a: !!str 123\nb: !!int '7'\nc: !!float '2'\n",
    "",
    "---\na: 1\n...\n",
    "a:\n  - b: 1\n    c: 2\n  - b: 3\n",
    "flow: {a: 1, b: [x, y]}\nempty:\nnone: null\n",
]
root = os.path.join(os.path.dirname(__file__), "..", "..")
for path in glob.glob(os.path.join(root, "**", "*.yaml"), recursive=True)[:200]:
    if "/target/" in path: continue
    try: texts.append(open(path, encoding="utf-8").read())
    except Exception: pass

def keys_of(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (str(k),)
            yield from keys_of(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield path + (str(i),)
            yield from keys_of(v, path + (str(i),))

reqs, expect = [], []
for t in texts:
    try:
        data = yaml.load(t, Loader=sources._yaml_loader())
        dj = json.dumps(data, default=str)
    except Exception:
        data, dj = None, "ERROR"
    keys = [".".join(k) for k in (keys_of(data) if dj != "ERROR" else []) if all("." not in s for s in k)]
    nlines = t.count("\n") + 1
    probes = [(random.choice(keys) if keys else None, random.randint(1, nlines)) for _ in range(6)]
    for key, line in probes:
        req = {"op": "pyyaml", "text": t, "line": line}
        if key is not None: req["key"] = key
        reqs.append(req)
        expect.append({"data": dj, "line": sources.yaml_line_of(t, key) if key is not None else None, "key_at": sources.yaml_key_at(t, line)})
r = subprocess.run([BIN], input="\n".join(json.dumps(q) for q in reqs), capture_output=True, text=True)
got = [json.loads(l) for l in r.stdout.splitlines()]
assert len(got) == len(expect), (len(got), len(expect), r.stderr[-2000:])
bad = [(q, e, g) for q, e, g in zip(reqs, expect, got) if e != g]
for q, e, g in bad[:8]:
    print("MISMATCH", json.dumps(q)[:300]); print("  python:", json.dumps(e)[:300]); print("  rust:  ", json.dumps(g)[:300])
print(f"{len(reqs) - len(bad)} of {len(reqs)} agree")
sys.exit(1 if bad else 0)
