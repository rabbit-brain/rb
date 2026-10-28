"""Write rust/src/assets/mcp.json: what `rb mcp` says of itself. The protocol versions, the instructions, every tool
(its command words, positional arguments, options and JSON Schema) and the resources come from rust/src/mcp_tools.py;
the two resources that are the same text in every project (rb://schema and rb://format) from the vocabulary
(rust/src/ir.py) and the format's schema (src/rabbit_brain/record.py). Run it after changing any of them:
python rust/gen/gen_mcp.py"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, SRC)
from rabbit_brain.record import format_schema
import mcp_tools as mcp
from ir import IR

OUT = os.path.join(SRC, "assets", "mcp.json")
SCHEMA_NOTE = ("the vocabulary rb ships, never an interpretation of this project; "
               "rb schema <kind> prints each record's full JSON Schema, rb://format the whole file format, "
               "and rb checks the provenance column on every write")


def text() -> str:
    """The file's whole text, as the declarations give it now."""
    tools = [{"name": t["name"], "description": t["description"], "words": mcp.cli_words(t["name"]), "args": t["args"],
              "opts": t.get("opts") or {}, "props": t["props"], "required": t.get("required", [])} for t in mcp.TOOLS]
    data = {
        "protocols": mcp.PROTOCOLS,
        "instructions": mcp.INSTRUCTIONS,
        "tools": tools,
        "resources": mcp.RESOURCES,
        "schema_text": json.dumps({"objects": IR, "note": SCHEMA_NOTE}, indent=1),
        "format_text": json.dumps(format_schema(), indent=1),
    }
    return json.dumps(data, ensure_ascii=False, indent=None, separators=(",", ":"))


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text())
    print(len(mcp.TOOLS), "tools,", len(mcp.RESOURCES), "resources")
