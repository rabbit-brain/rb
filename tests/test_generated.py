"""What the binary carries of rb's declarations is what they give now.

The command line (rust/src/cli_parser.py), the vocabulary (rust/src/ir.py), what rb mcp says of itself
(rust/src/mcp_tools.py), the record's shapes (src/rabbit_brain/record.py) and the table of error codes
(src/rabbit_brain/errors.py) are each written into a file the binary is built from, by a generator in rust/gen. A
change to a declaration that was not written out would leave the binary on the old one, so each file is held to its
generator here."""
import importlib.util

import pytest

from rbtest import ROOT

GEN = ROOT / "rust" / "gen"
WRITTEN = {
    "gen_cli_spec": "rust/src/cli_spec.json",
    "gen_schemas": "rust/src/schemas.json",
    "gen_mcp": "rust/src/assets/mcp.json",
    "gen_errors": "rust/src/errors_table.rs",
    "gen_record": "rust/src/record_gen.rs",
}


@pytest.mark.skipif(not GEN.is_dir(), reason="the generators stay in the repository: nothing to write from here")
@pytest.mark.parametrize("generator", sorted(WRITTEN))
def test_a_file_the_binary_is_built_from_is_what_its_generator_gives(generator):
    spec = importlib.util.spec_from_file_location(f"rb_{generator}", GEN / f"{generator}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (ROOT / WRITTEN[generator]).read_text(encoding="utf-8") == module.text(), \
        f"{WRITTEN[generator]} is stale: python rust/gen/{generator}.py"
