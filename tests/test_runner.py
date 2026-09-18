"""The runner end to end: the synthetic adapter on any machine, and the RAFT adapter's mechanics when torch and a RAFT checkout are available."""
import json
import os
import shutil
from pathlib import Path

import pytest

from rabbit_brain.cli import main
from rabbit_brain.models import Envelope


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def run_json(capsys, *argv):
    code, out, err = run(capsys, *argv, "--json")
    return code, Envelope.model_validate_json(out), err


def test_synthetic_demo_end_to_end(workdir, capsys):
    code, env, _ = run_json(capsys, "init", "--demo")
    assert code == 0 and Path("rb.toml").exists() and Path("ckpt/synth-current.json").exists() and "rb-runs/*/evidence/" in Path(".gitignore").read_text()
    code, env, _ = run_json(capsys, "doctor")
    assert code == 0 and env.data["ok"] and {c["check"] for c in env.data["checks"]} >= {"config", "adapter", "dataset", "hook"}
    code, env, _ = run_json(capsys, "verify-hook", "--checkpoint", "ckpt/synth-candidate.json")
    assert code == 0 and env.data["ok"] and env.data["fired"] == 12 == env.data["expected"]
    code, env, _ = run_json(capsys, "run", "--baseline", "ckpt/synth-current.json", "--candidate", "ckpt/synth-candidate.json", "--quiet")
    assert code == 0 and env.ok and env.run_id
    run_id = env.run_id
    s = env.data["summary"]
    assert s["cases"] == 24 and s["with_gt"] == 24 and s["with_trajectories"] == 24 and s["regressions"] >= 1 and s["unstable"] >= 1
    assert env.data["hook"]["status"] == "recorded" and env.data["hook"]["verified"] is True
    record = json.loads((workdir / "rb-runs" / run_id / "record.json").read_text())
    assert record["source"] == "run" and record["command"].startswith("rb run --baseline") and len(record["checkpoints"]["candidate"]["sha256"]) == 64
    assert record["seeds"]["python"] == 0 and "Synthetic" in record["notes"] and record["adapter"]["id"] == "synthetic"
    report = (workdir / "rb-runs" / run_id / "report.md").read_text()
    assert "Synthetic adapter" in report and "Candidate checkpoint: ckpt/synth-candidate.json (sha256" in report
    # the rest of the workflow works on a run the runner wrote
    code, env, _ = run_json(capsys, "findings", run_id, "--top", "3")
    assert code == 0 and len(env.data["queue"]) == 3
    top = env.data["queue"][0]["id"]
    code, env, _ = run_json(capsys, "check", "save", run_id, top)
    assert code == 0
    code, env, _ = run_json(capsys, "check", "run", run_id, "--fail-on", "checks")
    assert code in (0, 1)
    # deterministic: a second run with the same seed produces the same numbers
    code, env2, _ = run_json(capsys, "run", "--baseline", "ckpt/synth-current.json", "--candidate", "ckpt/synth-candidate.json", "--quiet")
    a = json.loads((workdir / "rb-runs" / run_id / "bundle.json").read_text())["cases"]
    b = json.loads((workdir / "rb-runs" / env2.run_id / "bundle.json").read_text())["cases"]
    assert [(c["id"], c["candidate_error"], c["candidate_trajectory"]) for c in a] == [(c["id"], c["candidate_error"], c["candidate_trajectory"]) for c in b]


def test_run_flags_and_errors(workdir, capsys):
    code, env, _ = run_json(capsys, "run", "--baseline", "a", "--candidate", "b")
    assert code == 2 and env.errors[0].code == "E_CONFIG_MISSING"
    run_json(capsys, "init", "--demo")
    code, env, _ = run_json(capsys, "run", "--baseline", "ckpt/missing.json", "--candidate", "ckpt/synth-candidate.json")
    assert code == 2 and env.errors[0].code == "E_CHECKPOINT_NOT_FOUND"
    code, env, _ = run_json(capsys, "run", "--baseline", "ckpt/synth-current.json", "--candidate", "ckpt/synth-candidate.json", "--limit", "5", "--no-trajectories", "--quiet")
    assert code == 0 and env.data["summary"]["cases"] == 5 and env.data["summary"]["with_trajectories"] == 0 and env.data["hook"]["status"] == "disabled"
    code, env, _ = run_json(capsys, "run", "--baseline", "ckpt/synth-current.json", "--candidate", "ckpt/synth-candidate.json", "--fail-on", "flags", "--quiet")
    assert code == 1 and env.ok and env.data["failed"] is True
    code, env, _ = run_json(capsys, "init", "--demo")
    assert code == 2 and env.errors[0].code == "E_CONFIG_INVALID"
    code, env, _ = run_json(capsys, "rerun", "x", "y")
    assert code == 2 and env.errors[0].code == "E_NOT_AVAILABLE"


def test_custom_adapter_must_implement_the_contract(workdir, capsys):
    Path("rb.toml").write_text('[project]\nname = "x"\n[adapter]\nmodule = "json:JSONDecoder"\n[dataset]\nname = "d"\n')
    code, env, _ = run_json(capsys, "doctor")
    assert code == 3 and any(c["check"] == "adapter" and c["status"] == "fail" for c in env.data["checks"])
    Path("rb.toml").write_text('[project]\nname = "x"\n[adapter]\nid = "nope"\n[dataset]\nname = "d"\n')
    code, env, _ = run_json(capsys, "verify-hook")
    assert code == 2 and env.errors[0].code == "E_CONFIG_INVALID"


# ---------------------------------------------------------------- RAFT mechanics (real code, random weights, CPU)

RAFT_PATH = os.environ.get("RB_RAFT_PATH") or (str(Path(__file__).resolve().parents[2] / "raft") if (Path(__file__).resolve().parents[2] / "raft" / "core" / "raft.py").exists() else None)
torch = pytest.importorskip("torch") if RAFT_PATH else None
cv2 = pytest.importorskip("cv2") if RAFT_PATH else None


@pytest.mark.skipif(not RAFT_PATH, reason="set RB_RAFT_PATH to a princeton-vl/RAFT checkout (needs torch, opencv, scipy)")
def test_raft_adapter_mechanics(workdir, capsys):
    import argparse
    import sys

    import numpy as np

    root = Path("data/kitti")
    (root / "image_2").mkdir(parents=True)
    (root / "flow_occ").mkdir(parents=True)
    rng = np.random.default_rng(0)
    H, W = 128, 256
    for i in range(3):
        img = cv2.GaussianBlur((rng.random((H, W, 3)) * 255).astype(np.uint8), (5, 5), 0)
        dx, dy = 2 + i, 1
        img2 = np.roll(np.roll(img, dy, axis=0), dx, axis=1)
        cv2.imwrite(str(root / "image_2" / f"{i:06d}_10.png"), img)
        cv2.imwrite(str(root / "image_2" / f"{i:06d}_11.png"), img2)
        if i < 2:  # third pair stays unlabeled
            u = np.full((H, W), dx, np.float32); v = np.full((H, W), dy, np.float32)
            valid = np.ones((H, W), np.uint16); valid[:8, :] = 0
            enc = np.stack([valid, (v * 64 + 2**15).astype(np.uint16), (u * 64 + 2**15).astype(np.uint16)], axis=-1)
            cv2.imwrite(str(root / "flow_occ" / f"{i:06d}_10.png"), enc)
    sys.path.insert(0, str(Path(RAFT_PATH) / "core"))
    from raft import RAFT  # type: ignore[import-not-found]
    Path("ckpt").mkdir()
    for name, seed, small in (("raft-a", 1, True), ("raft-b", 2, False)):  # a raft-small and a full RAFT, compared in one run
        torch.manual_seed(seed)
        m = RAFT(argparse.Namespace(small=small, mixed_precision=False, alternate_corr=False, dropout=0))
        torch.save({"module." + k: v for k, v in m.state_dict().items()}, f"ckpt/{name}.pth")

    code, env, _ = run_json(capsys, "init", "--project", "raft-mechanics", "--adapter", "raft", "--model-code", RAFT_PATH, "--dataset", str(root), "--device", "cpu", "--small")
    assert code == 0
    code, env, _ = run_json(capsys, "doctor", "--checkpoint", "ckpt/raft-a.pth")
    assert code == 0, env.data
    code, env, _ = run_json(capsys, "verify-hook", "--checkpoint", "ckpt/raft-a.pth", "--device", "cpu")
    assert code == 0 and env.data["fired"] == 12 and env.data["case"] == "000000_10", env.data
    code, env, _ = run_json(capsys, "run", "--baseline", "ckpt/raft-a.pth", "--candidate", "ckpt/raft-b.pth", "--device", "cpu", "--quiet")
    assert code == 0, env.errors
    s = env.data["summary"]
    assert s["cases"] == 3 and s["with_gt"] == 2 and s["with_trajectories"] == 3 and env.data["hook"]["verified"] is True
    record = json.loads((workdir / "rb-runs" / env.run_id / "record.json").read_text())
    assert record["adapter"]["id"] == "raft" and record["model_code"]["sha"] and record["environment"]["torch"]
    assert record["adapter"]["architectures"] == {"ckpt/raft-a.pth": "raft-small", "ckpt/raft-b.pth": "raft"}
    bundle = json.loads((workdir / "rb-runs" / env.run_id / "bundle.json").read_text())
    unlabeled = next(c for c in bundle["cases"] if c["id"] == "000002_10")
    assert unlabeled["has_gt"] is False and unlabeled.get("candidate_error") is None and len(unlabeled["candidate_trajectory"]) == 12
    labeled = next(c for c in bundle["cases"] if c["id"] == "000000_10")
    assert labeled["candidate_error"] > 0 and labeled["error_outcome"] in ("regression", "improved", "stable")
    # the paper's convergence statistics ride along for both models and land in the stability block
    conv = labeled["candidate_convergence"]
    assert 0 <= conv["sign_reversal_rate"] <= 1 and conv["displacement_mean"] >= 0 and conv["update_energy"] > 0
    st = labeled["stability"]["candidate"]
    assert st["last_update"] == labeled["candidate_trajectory"][-1] and st["sign_reversal_rate"] == conv["sign_reversal_rate"]
    # a report from a run carries the convergence section and the receipts wording
    report = (workdir / "rb-runs" / env.run_id / "report.md").read_text()
    assert "## Convergence on this case set" in report and "Evaluated by rb run" in report
    # rb case prints the absolute statistics
    code, env, text = run_json(capsys, "case", env.run_id, "000000_10")
    assert code == 0 and env.data["stability"]["candidate"]["displacement_max"] is not None
    # the architecture is read from the checkpoint, so a wrong `small` flag in rb.toml does not matter
    Path("rb.toml").write_text(Path("rb.toml").read_text().replace("small = true", "small = false"))
    code, env, _ = run_json(capsys, "verify-hook", "--checkpoint", "ckpt/raft-a.pth", "--device", "cpu")
    assert code == 0 and env.data["fired"] == 12, env.errors
    # a file that is not a RAFT checkpoint → a clear error, not a stack trace
    Path("ckpt/other.pth").write_bytes(b"not a checkpoint")
    code, env, _ = run_json(capsys, "verify-hook", "--checkpoint", "ckpt/other.pth", "--device", "cpu")
    assert code == 2 and env.errors[0].code == "E_CHECKPOINT_NOT_FOUND", env.errors
