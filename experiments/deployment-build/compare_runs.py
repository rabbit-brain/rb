
import json, sys
def load(p):
    d=json.load(open(p)); return {c["id"]:c for c in d["cases"]}, d
A,da = load(sys.argv[1]); B,db = load(sys.argv[2])
ids = sorted(set(A)&set(B))
print("cases:", len(A), len(B), "common", len(ids))
fields = ["baseline_error","candidate_error"]
worst = {}
exact = {f:0 for f in fields}
for f in fields:
    for i in ids:
        x,y = A[i][f], B[i][f]
        if x==y: exact[f]+=1
        worst[f]=max(worst.get(f,0.0), abs(x-y))
for f in fields:
    print(f"{f:18s} identical {exact[f]}/{len(ids)}   max |diff| {worst[f]:.3e}")
for f in ["baseline_trajectory","candidate_trajectory"]:
    n=0; md=0.0; same=0
    for i in ids:
        x,y = A[i].get(f) or [], B[i].get(f) or []
        if len(x)!=len(y): print("LENGTH MISMATCH", i, len(x), len(y)); continue
        n+=1
        d=max((abs(a-b) for a,b in zip(x,y)), default=0.0)
        if d==0.0: same+=1
        md=max(md,d)
    print(f"{f:18s} identical {same}/{n}   max |diff| {md:.3e}")
def tag(c):
    for t in (c.get("tags") or []):
        if t.startswith("b1="): return float(t[3:])
    return None
tb=[(tag(A[i]),tag(B[i])) for i in ids]
tb=[(x,y) for x,y in tb if x is not None and y is not None]
same=sum(1 for x,y in tb if x==y)
print(f"{'b1 tag':18s} identical {same}/{len(tb)}   max |diff| {max((abs(x-y) for x,y in tb), default=0.0):.3e}")
print("host old:", da["notes"]["host"][:60]); print("host new:", db["notes"]["host"][:60])
print("env old :", json.dumps(da["notes"]["environment"])[:200])
print("env new :", json.dumps(db["notes"]["environment"])[:200])
