"""Differential test: Rust's Python-number formatting against Python itself, on random and edge-case floats."""
import json, random, struct, subprocess, sys, math

BIN = sys.argv[1]
random.seed(7)

def bits(x): return format(struct.unpack("<Q", struct.pack("<d", x))[0], "016x")
def unbits(h): return struct.unpack("<d", struct.pack("<Q", int(h, 16)))[0]

xs = [0.0, -0.0, 1.0, -1.0, 0.1, 0.5, 1e-5, 1e-4, 1e16, 1e15, 123456789.0, 1.5e300, 5e-324, 2.2250738585072014e-308,
      1.7976931348623157e308, 0.125, 0.375, 2.675, 9.9999995, 999999.5, 1e22, 1e23, 0.0204, 1.43, 3.0e-7]
for _ in range(20000):
    k = random.random()
    if k < 0.3:
        x = struct.unpack("<d", struct.pack("<Q", random.getrandbits(64)))[0]
        if not math.isfinite(x): continue
    elif k < 0.6:
        x = random.uniform(-1000, 1000)
    elif k < 0.8:
        x = round(random.uniform(-100, 100), random.randint(0, 6))
    else:
        x = random.uniform(-1, 1) * 10 ** random.randint(-12, 20)
    xs.append(x)
specs = ["g", "+.4g", ".4g", ".3f", "+g", ".2f", ".1f", ".0f", "+.2f", "4.0f", "f", ".2%", "e", ".3e", ">9", "<12", "+.6g", ".12g"]
reqs, expect = [], []
for x in xs:
    reqs.append({"op": "repr", "x": bits(x)}); expect.append(repr(x))
    for sp in specs:
        reqs.append({"op": "fmt", "x": bits(x), "spec": sp}); expect.append(format(x, sp))
    n = random.randint(0, 6)
    reqs.append({"op": "round", "x": bits(x), "n": n}); expect.append(bits(round(x, n)))
for s in ["1_000.5", " 2.5 ", "inf", "-Infinity", "nan", "1e3", ".5", "5.", "1__0", "_1", "1e", "0x10", "1.5e+3", "+.5e-2"]:
    try: v = bits(float(s))
    except ValueError: v = None
    reqs.append({"op": "parse_float", "s": s}); expect.append(v)
r = subprocess.run([BIN], input="\n".join(json.dumps(q) for q in reqs), capture_output=True, text=True)
got = [json.loads(l) for l in r.stdout.splitlines()]
assert len(got) == len(expect), (len(got), len(expect), r.stderr[-500:])
bad = [(q, e, g) for q, e, g in zip(reqs, expect, got) if e != g and not (q["op"] == "parse_float" and e and g and math.isnan(unbits(e)) and math.isnan(unbits(g)))]
for q, e, g in bad[:20]:
    print("MISMATCH", q, "python:", e, "rust:", g)
print(f"{len(reqs) - len(bad)} of {len(reqs)} agree")
sys.exit(1 if bad else 0)
