"""Derive the Owner-tier example (rb page, rb export, rb sync, the hosted page) from the Live one (rb open).

Usage: python3 -I owner_of.py int8-demo.live.view.json > int8-demo.owner.view.json
Each change is one row of SPRINT-OCT24.md section 1.5 (Tiers: the Live example and the Owner document).
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # UTF-8 with LF, as the fixture is, whatever the terminal says (cp1252 on Windows)

doc = json.load(open(sys.argv[1], encoding="utf-8"))
QUOTED = re.compile(r' · "[^"]*"$')  # the user's own words at the end of a call line

doc["tier"], doc["surface"] = "owner", "page"                # the envelope: who it is for, and where it is drawn
doc.pop("digest", None)
doc.pop("places", None)                                      # place line, pickers, scope bar, Elsewhere: Live only
for f in doc["findings"].values():
    f["your_words"] = ""                                     # the user's words: never on a page
    f["observed"], f["observed_short"], f["observed_places"] = "", "", []  # a place's path and name: Live only
    f["rests_on"]["call"] = QUOTED.sub("", f["rests_on"]["call"])
for c in doc["made_calls"]:
    c["line"] = QUOTED.sub("", c["line"])
for q in doc["questions"]:                                   # each interpretation's calls say them as made_calls do
    for g in q["overview"]["interpretations"]:
        g["calls"] = [QUOTED.sub("", c) for c in g["calls"]]
    for rel in q["overview"].get("relations", []):           # the words given with a declaration: Live only
        for d in rel.get("declarations", []):
            d["words"] = ""
for r in doc["runs"].values():
    for k in ("observed_in", "recorded_in"):                 # a place's path and name: Live only, null where given
        if k in r:
            r[k] = None
    r["receipt_rows"] = [row for row in r["receipt_rows"] if row[0] != "platform"]  # the OS string: Live only
for c in doc.get("citations", {}).values():
    c["open_url"] = ""                                       # a link to the file on this machine: rb open only
for res in doc["resources"].values():
    res.pop("where_short", None)
    res["url"] = ""                                          # a download address only with rb export --url


PAGE_SAYS = ("changed since you confirmed it", "changed since it was confirmed")  # AGENTS.md: "a page says `changed since it was confirmed`"


def page_words(line: str) -> str:
    """One of rb's design lines as a page says it: only rb's own words, never text someone recorded."""
    if line.startswith(PAGE_SAYS[0]):
        return PAGE_SAYS[1] + line[len(PAGE_SAYS[0]):]
    return line[: -len(PAGE_SAYS[0])] + PAGE_SAYS[1] if line.endswith("; " + PAGE_SAYS[0]) else line


for x in doc["experiments"]:
    x["design"]["lines"] = [page_words(l) for l in x["design"]["lines"]]
for row in doc["pending"]["rows"]:
    row["text"] = page_words(row["text"])
    if "lines" in row:
        row["lines"] = [page_words(l) for l in row["lines"]]
doc["reproduce_url"] = ""                                    # rb export --url <address> sets it; Reproduce this project
json.dump(doc, sys.stdout, ensure_ascii=False, indent=1)
print()
