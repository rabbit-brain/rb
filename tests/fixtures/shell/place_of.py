"""Derive rb open's two other places from the Live example (rb open at main, live): away, the worktree exp-b as it is
now, and past, main at the version 3f2a9c1.

Usage: python3 -I place_of.py away int8-demo.live.view.json > int8-demo.away.view.json
       python3 -I place_of.py past int8-demo.live.view.json > int8-demo.past.view.json
Each change is a rule of BP phase 4b. The record's content stays the Live example's, so these two pin the place, the
scope bar, where each run was recorded and the commands, not what the record held there or then.
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # UTF-8 with LF, as the fixture is, whatever the terminal says (cp1252 on Windows)

state, doc = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
MAIN = {"id": "main", "label": "main · ~/int8-resnet50", "path": "/home/ana/int8-resnet50", "commit": "3f2a9c1"}
EXPB = {"id": "exp-b", "label": "exp-b · .worktrees/b", "path": "/home/ana/int8-resnet50/.worktrees/b", "commit": "5d1e0a7"}
RECORDED = {"runs3": EXPB}  # every other run was recorded in main
P = doc["places"]


def recorded_here(here):
    """Each run says where it was recorded when that is not the place shown; each finding says it of its counted runs."""
    for rid, r in doc["runs"].items():
        at = RECORDED.get(rid, MAIN)
        if at["id"] == here:
            r["observed_in"], r["recorded_in"] = None, None
        else:
            r["observed_in"] = {"label": f"recorded in {at['id']} · {at['path']} at {at['commit']}", "label_short": f"recorded in {at['label']} at {at['commit']}",
                                "commit": at["commit"], "dirty": False, "gone": False, "place": at["id"]}
            r["recorded_in"] = {"place": at["id"], "label": at["label"], "href": f"#run-{rid}&place={at['id']}"}
    for f in doc["findings"].values():
        counted = list(dict.fromkeys(o["run"] for o in f["observations"] if o["counted"]))
        away = [r for r in counted if RECORDED.get(r, MAIN)["id"] != here]
        f["observed"], f["observed_short"], f["observed_places"] = "", "", []
        if away:
            at = RECORDED.get(away[0], MAIN)
            of = f"{len(away)} of {len(counted)} {'run' if len(counted) == 1 else 'runs'} {'was' if len(away) == 1 else 'were'} recorded"
            f["observed"], f["observed_short"] = f"{of} in {at['id']} · {at['path']}", f"{of} in {at['label']}"
            f["observed_places"] = [{"id": at["id"], "label": at["label"]}]


if state == "away":
    # another place, now: its own scope bar, no Elsewhere (that is the live start place's), every command with its cd
    P["place"] = {"id": EXPB["id"], "label": EXPB["label"], "path": EXPB["path"], "start": False, "read_only": False, "version": None,
                  "line": "exp-b · .worktrees/b · 1 run not merged into main"}
    P["versions"] = []
    P["scope_bar"] = {"line": "Viewing exp-b · .worktrees/b", "back": "Back to main", "href": "#overview", "withheld": ""}
    P["elsewhere"] = []
    cd = f"cd {EXPB['path']} && "
    for c in doc["calls"]:
        c["do"] = cd + c["do"]
        for a in c["actions"]:
            a["line"] = cd + a["line"]
    for row in doc["pending"]["rows"]:
        row["confirm"] = cd + row["confirm"]
    recorded_here(EXPB["id"])
elif state == "past":
    # the start place at a past version: read only, said by the scope bar, and no command to copy (BP 4b)
    v = dict(P["versions"][0])
    v["shown"] = f"{MAIN['label']} as of {v['sha']} · {v['at']} · read only"
    P["place"].update(version=v, read_only=True, line="")
    P["scope_bar"] = {"line": v["shown"], "back": "Back to now", "href": "#overview",
                      "withheld": "A past version offers no command: a call copied here would be recorded on today's readings."}
    P["elsewhere"] = []
    for c in doc["calls"]:
        c["actions"], c["do"] = [], ""
    for row in doc["pending"]["rows"]:
        row["confirm"] = ""
    # a waiting design is said without the command that lists it (guarantee 38, M's view::as_past_version)
    for e in doc["experiments"]:
        lines = e.get("design", {}).get("lines", [])
        lines[:] = ["design not confirmed" if l.startswith("design not confirmed") else l for l in lines]
        if "confirm" in e.get("design", {}):
            e["design"]["confirm"] = ""              # places.rs withhold_commands blanks it
    doc["pending"]["designs_line"] = doc["pending"]["designs_line"].split(": rb confirm")[0].rstrip(".") + "."
    doc["home"]["found"], doc["home"]["found_total"] = [], 0
else:
    sys.exit("place_of.py away|past <live document>")
json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
print()
