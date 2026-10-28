"""Differential test: the statistics behind findings (fmean, stdev, variance, t quantiles, intervals, lgamma), Rust
against the reference implementation (reference.py) and CPython's statistics and math, bit for bit."""
import json, math, random, statistics, struct, subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reference as assess

BIN = sys.argv[1]
random.seed(4)
def bits(x): return format(struct.unpack("<Q", struct.pack("<d", x))[0], "016x")
reqs, expect = [], []
for trial in range(3000):
    n = random.randint(1, 12)
    kind = random.random()
    if kind < 0.3: xs = [round(random.uniform(-2, 2), random.randint(1, 4)) for _ in range(n)]
    elif kind < 0.6: xs = [random.gauss(0.02, 0.01) for _ in range(n)]
    elif kind < 0.8: xs = [random.uniform(-1e6, 1e6) for _ in range(n)]
    else: xs = [random.choice([0.1, 0.2, 0.3, 1e-9, 5.64, 5.62]) for _ in range(n)]
    ys = [random.gauss(0.5, 0.2) for _ in range(random.randint(0, 6))]
    df = random.choice([1, 2, 3, 4, 7, 10.5, 30, 2.7])
    reqs.append({"op": "stats", "xs": [bits(x) for x in xs], "ys": [bits(y) for y in ys], "df": df})
    e, lo, hi, why = assess.mean_interval(xs)
    exp = {"fmean": bits(statistics.fmean(xs)), "mi": [bits(e), bits(lo) if lo is not None else None, bits(hi) if hi is not None else None, why]}
    if len(xs) >= 2:
        exp["stdev"] = bits(statistics.stdev(xs)); exp["variance"] = bits(statistics.variance(xs))
    if ys:
        e, lo, hi, why = assess.welch_interval(xs, ys)
        exp["wi"] = [bits(e), bits(lo) if lo is not None else None, bits(hi) if hi is not None else None, why]
    exp["tppf"] = bits(assess.t_ppf(0.975, df)); exp["lgamma"] = bits(math.lgamma(df / 2 + 0.5))
    expect.append(exp)
r = subprocess.run([BIN], input="\n".join(json.dumps(q) for q in reqs), capture_output=True, text=True)
got = [json.loads(l) for l in r.stdout.splitlines()]
assert len(got) == len(expect), (len(got), len(expect), r.stderr[-2000:])
bad = [(q, e, g) for q, e, g in zip(reqs, expect, got) if e != g]
for q, e, g in bad[:5]:
    diff = {k: (e.get(k), g.get(k)) for k in e if e.get(k) != g.get(k)}
    print("MISMATCH", len(q["xs"]), "xs", len(q["ys"]), "ys, df", q["df"], diff)
print(f"{len(reqs) - len(bad)} of {len(reqs)} agree")
sys.exit(1 if bad else 0)
