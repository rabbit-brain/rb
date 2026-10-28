"""schema/rb-v1.schema.json is the format's contract for anyone without rb: it must be exactly what rb enforces.

The file is generated from the record models (`rb schema all`), committed so a reader, a CI job or another tool can
validate a record without installing rb; its $id is https://rabbitbrain.ai/schema/rb-v1.schema.json. If the models
change and the file does not, this fails; regenerate it with:

    rb schema all --json | python -c "import json,sys; print(json.dumps(json.load(sys.stdin)['data'], indent=2, ensure_ascii=False))" > schema/rb-v1.schema.json
"""
import json
from pathlib import Path

from rabbit_brain.record import SCHEMA_ID, format_schema

SCHEMA = Path(__file__).resolve().parents[1] / "schema" / "rb-v1.schema.json"


def test_the_committed_schema_is_the_one_rb_enforces():
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert committed == format_schema(), "schema/rb-v1.schema.json is stale: regenerate it (see this file's docstring)"
    assert committed["$id"] == SCHEMA_ID


def test_rb_schema_all_prints_it(capsys):
    from rbtest import main
    assert main(["schema", "all", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)["data"]
    assert printed == json.loads(SCHEMA.read_text(encoding="utf-8"))
