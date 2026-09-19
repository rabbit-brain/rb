
"""Recorder ablation analysis. Protocol: docs/deployment-experiment.md, amendment 2.

Scores every case with the product's own trajectory_stats, so late_update here is the same
number the product would compute. The only thing that differs between the two RB columns is
which channel filled the trajectory.
"""
import json, sys
from pathlib import Path
from rabbit_brain.stability import trajectory_stats

B1_THRESHOLD = 0.0295          # FROZEN.py, configuration-half median, chosen before any holdout case

def cases(p):
    return {c["id"]: c for c in json.load(open(p))["cases"]}

def b1(c):
    for t in (c.get("tags") or []):
        if t.startswith("b1="):
            return float(t[3:])
    return None

def late(c, side):
    t = c.get(side + "_trajectory")
    return trajectory_stats(t).late_update if t else None

def auroc(scores, labels):
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return float("nan")
    n = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pos for b in neg)
    return n / (len(pos) * len(neg))

def precision_at(scores, labels, k):
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
    return sum(1 for i in order if labels[i]) / max(1, len(order))

def spearman(x, y):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")

def report(name, coarse_path, corrected_path):
    C, F = cases(coarse_path), cases(corrected_path)
    ids = sorted(set(C) & set(F))
    rows = []
    for i in ids:
        c, f = C[i], F[i]
        d = c["candidate_error"] - c["baseline_error"]
        rows.append({"id": i, "worse": d > 0.0, "delta": d, "b1": b1(c),
                     "rb_coarse": late(c, "candidate"), "rb_corrected": late(f, "candidate")})
    rows = [r for r in rows if None not in (r["b1"], r["rb_coarse"], r["rb_corrected"])]
    print("")
    print("=" * 78)
    print(name + "   n = " + str(len(rows)))
    lab = [r["worse"] for r in rows]
    print("  base rate worse: %.2f   (%d worse, %d better)" % (sum(lab)/len(lab), sum(lab), len(lab)-sum(lab)))
    ratios = sorted(r["rb_corrected"] / r["rb_coarse"] for r in rows if r["rb_coarse"])
    q = lambda p: ratios[min(len(ratios)-1, int(p*len(ratios)))]
    print("  P1  corrected/coarse late_update: median %.4f  [p10 %.4f, p90 %.4f]  max %.4f"
          % (q(0.5), q(0.10), q(0.90), ratios[-1]))
    print("  P2  spearman(rb_coarse, rb_corrected) = %.4f"
          % spearman([r["rb_coarse"] for r in rows], [r["rb_corrected"] for r in rows]))
    for tag, sel in (("unrestricted", rows),
                     ("restricted (b1 >= %.4f)" % B1_THRESHOLD, [r for r in rows if r["b1"] >= B1_THRESHOLD])):
        if not sel:
            continue
        L = [r["worse"] for r in sel]
        if len(set(L)) < 2:
            print("  %-28s n=%-4d all one class, skipped" % (tag, len(sel)))
            continue
        print("  %-28s n=%-4d base rate %.2f" % (tag, len(sel), sum(L)/len(L)))
        print("      %-14s %8s %8s %8s" % ("score", "AUROC", "prec@10", "prec@20"))
        for label, key in (("B1", "b1"), ("RB coarse", "rb_coarse"), ("RB corrected", "rb_corrected")):
            S = [r[key] for r in sel]
            print("      %-14s %8.3f %8.2f %8.2f" % (label, auroc(S, L), precision_at(S, L, 10), precision_at(S, L, 20)))

base = Path("/workspace/exp/ablation")
report("MIXED PRECISION, held out", base / "ref-vs-mixed.holdout.json", base / "ref-vs-mixed.holdout.corrected.json")
report("TRUNCATION 12 to 8, held out", base / "ref-vs-trunc.holdout.json", base / "ref-vs-trunc.holdout.corrected.json")
report("MIXED PRECISION, configuration half", base / "ref-vs-mixed.config.A.json", base / "ref-vs-mixed.config.A.corrected.json")
report("TRUNCATION 12 to 8, configuration half", base / "ref-vs-trunc.config.json", base / "ref-vs-trunc.config.corrected.json")
