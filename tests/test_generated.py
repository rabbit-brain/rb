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
    "gen_script": "rust/src/shell/assets/script.min.js",
}


@pytest.mark.skipif(not GEN.is_dir(), reason="the generators stay in the repository: nothing to write from here")
@pytest.mark.parametrize("generator", sorted(WRITTEN))
def test_a_file_the_binary_is_built_from_is_what_its_generator_gives(generator):
    spec = importlib.util.spec_from_file_location(f"rb_{generator}", GEN / f"{generator}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (ROOT / WRITTEN[generator]).read_text(encoding="utf-8") == module.text(), \
        f"{WRITTEN[generator]} is stale: python rust/gen/{generator}.py"


def _gen_script():
    spec = importlib.util.spec_from_file_location("rb_gen_script", GEN / "gen_script.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not GEN.is_dir(), reason="the generators stay in the repository")
def test_the_inlined_script_is_script_js_less_its_comment_lines_and_indentation():
    """The page script rb page and rb open inline (script.min.js) is script.js with each whole-line comment and each
    line's leading indentation taken out, and nothing else: every other line, in order, as written; a line that starts
    in a template literal or a block comment kept whole (the orchestrator's ruling of Nov 12 07:05 +08)."""
    g = _gen_script()
    src = (ROOT / "rust/src/shell/assets/script.js").read_text(encoding="utf-8")
    kept = [ln for ln in g.squeeze(src).split("\n") if ln]
    i = 0
    for ln in src.split("\n"):
        if i < len(kept) and kept[i] in (ln, ln.lstrip()):
            i += 1
        else:
            assert not ln.strip() or ln.strip().startswith("//"), ln
    assert i == len(kept)
    # a template literal's lines keep their indentation and their "//"
    assert g.squeeze("const a = `\n  // kept\n`;\n// gone\n  b();\n") == "const a = `\n  // kept\n`;\nb();\n"
    # a block comment's lines are kept as written
    assert g.squeeze("/* one\n  two */\nx();\n") == "/* one\n  two */\nx();\n"
    # a string or a regular expression that runs past its line is refused
    for bad in ("const s = 'a\\\n b';\n", "const r = /a\\\nb/;\n"):
        with pytest.raises(g.Unsqueezable):
            g.squeeze(bad)
    # a regex holding a quote or a backtick is no string
    assert g.squeeze("x = s.replace(/[`'\"]/g, '');\n  y();\n") == "x = s.replace(/[`'\"]/g, '');\ny();\n"
