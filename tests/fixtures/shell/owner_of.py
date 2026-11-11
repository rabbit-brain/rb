"""Derive the Owner-tier example (rb page, rb export, rb sync, the hosted page) from the Live one (rb open).

Usage: python3 -I owner_of.py int8-demo.live.view.json > int8-demo.owner.view.json
Each change is one row of SPRINT-OCT24.md section 1.5 (Tiers: the Live example and the Owner document).
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # UTF-8 with LF, as the fixture is, whatever the terminal says (cp1252 on Windows)

doc = json.load(open(sys.argv[1], encoding="utf-8"))
ABSENT = ("which this repository does not have", "which the repository this page was made from did not have")


def page_rows(rows: list) -> list:
    """A run's receipt rows as a page says them (EVIDENCE.md E2, E5): a given tree its repository lacks in the page's
    form, and its evidence lines led once by "When this page was made:", "here" dropped, "not in this clone" said of
    the repository the page was made from."""
    out, lines, at = [], [], None
    for row in rows:
        if row[0] == "code":
            row = [row[0], row[1].replace(*ABSENT), row[2]]
        if row[0] != "evidence":
            out.append(row)
            continue
        at = len(out) if at is None else at
        line = row[1].replace("not in this clone", "not in the repository this page was made from").rstrip(".").replace(" here", "")
        line = line.replace("Files it named: ", "files it named ").replace("Config: ", "config ")
        lines.append(line[:1].lower() + line[1:])
    if at is not None:
        out.insert(at, ["evidence", "When this page was made: " + "; ".join(lines) + ".", []])
    return out

QUOTED = re.compile(r' · "[^"]*"$')  # the user's own words at the end of a call line

doc["tier"], doc["surface"] = "owner", "page"                # the envelope: who it is for, and where it is drawn
doc.pop("digest", None)
doc.pop("places", None)                                      # place line, pickers, scope bar, Elsewhere: Live only
c = doc["checkout"]                                          # a page's checkout: its words and commit, no branch or rule
doc["checkout"] = {"state": c["state"], "said": c["said"], "commit": c["commit"], "line": c["page_line"]}
for k in ("record_files", "page_evidence"):                  # About's block; the evidence, check and names: Live only
    if k in c:
        doc["checkout"][k] = c[k]
for f in doc["findings"].values():
    f["your_words"] = ""                                     # the user's words: never on a page
    if f["says"].get("evidence"):                            # the reading's evidence (E5), as of when the page was made
        e = f["says"]["evidence"].removeprefix("Evidence: ").replace("not in this clone", "not in the repository this page was made from").replace("gone from this clone", "gone from the repository this page was made from")
        f["says"]["evidence"] = "When this page was made: " + e
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
    r["receipt_rows"] = page_rows(r["receipt_rows"])         # a run's evidence as of when the page was made (E5)
for x in doc["experiments"]:                                 # your checkout against the code a step names: rb open only
    for st in x["reproduce"]["steps"]:
        st.pop("checkout", None)
for r in doc["runs"].values():                               # the run's own code step and your checkout under it: rb open only
    r["reproduce"].pop("code_step", None)
    for st in r["reproduce"]["steps"]:
        st.pop("checkout", None)
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
