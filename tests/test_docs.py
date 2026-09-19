"""AGENTS.md and the CLI must agree by construction: every command the doc names exists, every error code it lists is real, and nothing real is undocumented."""
import re
from pathlib import Path

from rabbit_brain.cli import COMMANDS, PLANNED, build_parser, docs_text
from rabbit_brain.errors import ERRORS

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "AGENTS.md").read_text(encoding="utf-8")


def registered_commands() -> set[str]:
    parser = build_parser()
    names = set()
    for action in parser._subparsers._group_actions:
        for name, sub in action.choices.items():
            names.add(name)
            for a in getattr(sub, "_subparsers", None)._group_actions if getattr(sub, "_subparsers", None) else []:
                for subname in a.choices:
                    names.add(f"{name} {subname}")
    return names


def test_every_command_in_the_doc_exists():
    mentioned = set(re.findall(r"`rb ([a-z-]+(?: (?:save|run|list|rm))?)", DOC))
    mentioned |= set(re.findall(r"^rb ([a-z-]+(?: (?:save|run|list|rm))?)", DOC, flags=re.M))
    real = registered_commands()
    for m in mentioned:
        base = m.split(" ")[0]
        assert m in real or base in PLANNED or m in {"check list|rm", "check list", "check rm"}, f"AGENTS.md mentions `rb {m}` which does not exist"


def test_every_real_command_is_documented():
    for c in COMMANDS:
        assert re.search(rf"rb {re.escape(c)}\b", DOC) or (c in ("check list", "check rm") and "check list|rm" in DOC), f"{c} is not in AGENTS.md"


def test_error_codes_agree():
    in_doc = set(re.findall(r"`(E_[A-Z_]+)`", DOC))
    assert in_doc == set(ERRORS), f"doc/code mismatch: doc-only {in_doc - set(ERRORS)}, code-only {set(ERRORS) - in_doc}"


def test_rb_docs_prints_the_same_file():
    assert docs_text() == DOC


def test_doc_states_the_rule_agents_must_follow():
    assert "Do not compute errors, regressions, rankings, stability or verdicts yourself" in DOC
    assert "For humans: verify what your agent did" in DOC


def test_custom_adapter_skeleton_in_the_doc_runs(workdir, capsys):
    """The adapter AGENTS.md prints for a custom model is a working adapter: rb doctor, verify-hook, verify-adapter and run all pass with it
    against the toy iterative model under tests/toymodel (its own eval.py is the reference path)."""
    import json
    import shutil
    import sys

    pytest = __import__("pytest")
    torch = pytest.importorskip("torch")
    np = pytest.importorskip("numpy")
    from rabbit_brain.cli import main
    from rabbit_brain.models import Envelope

    block = re.search(r"```python\n(from pathlib import Path\nimport numpy as np, torch\n.*?)```", DOC, re.S).group(1)
    code = block.replace("from mymodel.model import MyModel", "from model import IterFlow as MyModel")
    Path("rb_adapter.py").write_text(code, encoding="utf-8")
    shutil.copytree(ROOT / "tests" / "toymodel", "toymodel")
    Path("data/val").mkdir(parents=True)
    rng = np.random.default_rng(3)
    for i in range(4):
        f1 = (rng.random((3, 24, 32)) * 255).astype(np.uint8)
        dx, dy = 2.0, 1.0
        f2 = np.roll(np.roll(f1, 1, axis=1), 2, axis=2)
        np.savez(f"data/val/s{i}.npz", frame1=f1, frame2=f2, field=np.stack([np.full((24, 32), dx, np.float32), np.full((24, 32), dy, np.float32)]))
    sys.path.insert(0, str(Path("toymodel").resolve()))
    from model import IterFlow
    Path("ckpt").mkdir()
    for name, seed in (("current", 1), ("candidate", 2)):
        torch.manual_seed(seed)
        torch.save(IterFlow().state_dict(), f"ckpt/{name}.pt")

    def run_json(*argv):
        code = main([*argv, "--json"])
        out = capsys.readouterr().out
        return code, Envelope.model_validate_json(out)

    code, env = run_json("init", "--project", "toy", "--task", "flow", "--adapter", "rb_adapter:MyAdapter", "--model-code", "./toymodel", "--dataset", "./data/val", "--kind", "npz", "--iterations", "8", "--device", "cpu")
    assert code == 0, env.errors
    assert "small =" not in Path("rb.toml").read_text()          # RAFT-only keys stay out of a custom adapter's config
    code, env = run_json("doctor", "--checkpoint", "ckpt/current.pt", "--checkpoint", "ckpt/candidate.pt")
    assert code == 0 and env.data["ok"], env.data["checks"]
    assert any("parameters" in c["detail"] and "M parameters" not in c["detail"] for c in env.data["checks"] if c["check"].startswith("checkpoint"))
    code, env = run_json("verify-hook", "--checkpoint", "ckpt/current.pt")
    assert code == 0 and env.data["fired"] == 8, env.errors
    code, env = run_json("verify-adapter", "--checkpoint", "ckpt/candidate.pt")
    assert code == 0 and env.data["status"] == "agree" and env.data["cases"] == 4 and "eval.py" in env.data["reference"], env.data
    code, env = run_json("run", "--baseline", "ckpt/current.pt", "--candidate", "ckpt/candidate.pt", "--quiet")
    assert code == 0 and env.data["summary"]["cases"] == 4 and env.data["hook"]["verified"], env.errors
    record = json.loads((workdir / "rb-runs" / env.run_id / "record.json").read_text())
    assert record["checkpoints"]["candidate"]["architecture"] == "MyModel(hidden=16)" and record["adapter_agreement"]["candidate"]["status"] == "agree"
    assert record["dataset"]["content_hash"] and len(record["dataset"]["content_hash"]) == 64
