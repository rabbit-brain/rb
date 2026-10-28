"""Write rust/src/schemas.json: what `rb schema <name>` prints and returns for every name. The record shapes are
pydantic's for the Python models (src/rabbit_brain/record.py, models.py), which the SDK returns and the binary reads and
writes; the vocabulary is rust/src/ir.py. Run it after changing either: python rust/gen/gen_schemas.py"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
sys.path.insert(0, SRC)
from rabbit_brain.models import SCHEMAS
from rabbit_brain.record import format_schema
from cli_parser import SCHEMA_NAMES
from ir import IR

OUT = os.path.join(SRC, "schemas.json")
assert SCHEMA_NAMES == list(SCHEMAS), "rb schema's names (rust/src/cli_parser.py) are not the records there are (rabbit_brain.models.SCHEMAS)"


def said(name: str) -> dict:
    """What rb schema <name> prints (its lines) and returns under --json (its data)."""
    if name == "all":
        schema = format_schema()
        return {"lines": [json.dumps(schema, indent=2, ensure_ascii=False)], "data": schema}
    if name == "ir":
        lines = ["rb's vocabulary: every record kind, what it means, the command that writes it, and the sources rb requires.", ""]
        for kind, row in IR.items():
            lines += [f"{kind:<12} {row['thing']}", f"{'':<12} {row['is']}", f"{'':<12} write: {row['write']}", f"{'':<12} provenance: {row['provenance']}"]
        return {"lines": lines, "data": {"objects": IR, "note": "rb schema <kind> prints each kind's full JSON Schema; rb checks the provenance column on every write"}}
    schema = SCHEMAS[name].model_json_schema()
    return {"lines": [json.dumps(schema, indent=2)], "data": schema}


def text() -> str:
    """The file's whole text, as the models and the vocabulary give it now."""
    return json.dumps({name: said(name) for name in ["all", *SCHEMA_NAMES, "ir"]}, ensure_ascii=False, indent=None, separators=(",", ":"))


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text())
    print(len(SCHEMA_NAMES) + 2, "schemas")
