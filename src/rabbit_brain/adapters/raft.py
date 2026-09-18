"""Reference adapter for RAFT-family optical flow (princeton-vl/RAFT and forks with the same `core/` layout).

Model code: a checkout of the RAFT repository (`[adapter] model_code = "./raft"`); its `core/` directory is put on
sys.path because RAFT's modules import each other by bare name. Checkpoints: RAFT's `.pth` files (DataParallel
state dicts are accepted). Whether a checkpoint is raft-small or full RAFT is read from the state dict itself
(`update_block.mask.*` exists only in the full model), so one run can compare raft-things with raft-small; the
receipt records the architecture of each checkpoint. Dataset: KITTI-style directories, `image_2/*_10.png` +
`*_11.png`, and optionally `flow_occ/*_10.png` for ground truth; without `flow_occ` the cases are unlabeled and
only stability is assessed.

The trajectory is recorded without touching RAFT's code: a forward hook on `model.update_block`, whose output is
`(net, up_mask, delta_flow)`. Per-case error is RAFT's own KITTI evaluation: mean endpoint error over valid pixels.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from glob import glob
from pathlib import Path
from typing import Any, Iterable, Optional

from .. import __version__
from ..config import Config
from ..errors import RBError
from ..models import Metric
from ..recorder import TrajectoryRecorder
from .base import Case, Prediction, read_case_selector


class RaftAdapter:
    task = "flow"
    metric = Metric(id="mean_endpoint_error", name="mean endpoint error", unit="px")
    synthetic = False

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.model_code = Path(cfg.adapter.model_code or "./raft")
        self.iterations = cfg.adapter.iterations
        self.small = cfg.adapter.small
        self.mixed_precision = cfg.adapter.mixed_precision
        self.alternate_corr = cfg.adapter.alternate_corr
        self.kind = cfg.dataset.kind
        self.data_path = Path(cfg.dataset.path or "./data/kitti2015/training")
        self._raft = None
        self._utils = None
        self._frame_utils = None
        self._torch = None
        self.architectures: dict[str, str] = {}  # checkpoint path -> "raft" or "raft-small", filled by load()

    # ---- imports

    def _import(self) -> None:
        if self._raft is not None:
            return
        core = self.model_code / "core"
        if not (core / "raft.py").exists():
            raise RBError("E_MODEL_CODE_MISSING", message=f"{self.model_code} does not contain core/raft.py. Point [adapter] model_code at a RAFT checkout.")
        try:
            import torch  # noqa: F401
        except ImportError as exc:
            raise RBError("E_ADAPTER_IMPORT", message=f"torch is not importable in this environment ({exc}).")
        if str(core) not in sys.path:
            sys.path.insert(0, str(core))
        try:
            import raft as raft_module  # type: ignore[import-not-found]
            from utils import frame_utils  # type: ignore[import-not-found]
            from utils import utils as raft_utils  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RBError("E_ADAPTER_IMPORT", message=f"RAFT's modules did not import from {core}: {exc}")
        import torch
        self._torch, self._raft, self._utils, self._frame_utils = torch, raft_module, raft_utils, frame_utils

    def describe(self) -> dict:
        d = {
            "id": "raft", "version": __version__, "model_code": str(self.model_code), "git_sha": git_sha(self.model_code),
            "iterations": self.iterations, "small": self.small, "mixed_precision": self.mixed_precision, "alternate_corr": self.alternate_corr,
            "hook": "forward hook on model.update_block (output index 2 = delta_flow)",
        }
        if self.architectures:
            d["architectures"] = dict(self.architectures)  # detected per checkpoint; `small` above only sets random-weight models
        return d

    # ---- model

    def load(self, checkpoint: Path, device: str) -> Any:
        self._import()
        torch = self._torch
        if not checkpoint.exists():
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"No such checkpoint: {checkpoint}")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RBError("E_DEVICE", message="CUDA is not available in this environment.")
        try:
            try:
                state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)  # RAFT checkpoints are plain state dicts
            except TypeError:  # torch < 1.13 has no weights_only
                state = torch.load(str(checkpoint), map_location="cpu")
        except Exception as exc:  # noqa: BLE001  (torch raises several types for a corrupt or foreign file)
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"{checkpoint} is not a torch checkpoint: {str(exc)[:200]}")
        if isinstance(state, dict) and "state_dict" in state and isinstance(state["state_dict"], dict):
            state = state["state_dict"]
        if not isinstance(state, dict) or not state:
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"{checkpoint} does not contain a state dict.")
        state = {k[7:] if k.startswith("module.") else k: v for k, v in state.items()}
        small = is_small_state_dict(state)
        args = argparse.Namespace(small=small, mixed_precision=self.mixed_precision, alternate_corr=self.alternate_corr, dropout=0)
        model = self._raft.RAFT(args)
        try:
            model.load_state_dict(state)
        except RuntimeError as exc:
            arch = "raft-small" if small else "raft"
            raise RBError("E_CHECKPOINT_NOT_FOUND", message=f"{checkpoint} does not match RAFT's architecture (read as {arch} from its keys): {str(exc)[:200]}")
        self.architectures[str(checkpoint)] = "raft-small" if small else "raft"
        model.to(device).eval()
        return model

    def new_model(self, device: str = "cpu") -> Any:
        """A randomly initialised model, for hook verification when no checkpoint is at hand."""
        self._import()
        args = argparse.Namespace(small=self.small, mixed_precision=self.mixed_precision, alternate_corr=self.alternate_corr, dropout=0)
        return self._raft.RAFT(args).to(device).eval()

    # ---- data

    def cases(self) -> Iterable[Case]:
        ids, limit = read_case_selector(self.cfg)
        if self.kind != "kitti":
            raise RBError("E_DATASET_EMPTY", message=f"dataset.kind '{self.kind}' is not supported by the raft adapter (use kitti).")
        root = self.data_path
        images1 = sorted(glob(str(root / "image_2" / "*_10.png")))
        images2 = sorted(glob(str(root / "image_2" / "*_11.png")))
        flows = {Path(p).name: p for p in glob(str(root / "flow_occ" / "*_10.png"))}
        if not images1 or len(images1) != len(images2):
            raise RBError("E_DATASET_EMPTY", message=f"No KITTI pairs under {root}/image_2 (need *_10.png and *_11.png).")
        n = 0
        for img1, img2 in zip(images1, images2):
            cid = Path(img1).stem
            if ids is not None and cid not in ids:
                continue
            if limit is not None and n >= limit:
                break
            n += 1
            yield Case(id=cid, name=cid, inputs=(img1, img2), gt=flows.get(Path(img1).name), tags=["kitti"])

    def sample_inputs(self) -> tuple:
        """A random image pair for mechanics checks (rb verify-hook without a dataset)."""
        self._import()
        torch = self._torch
        g = torch.Generator().manual_seed(0)
        return (torch.rand(1, 3, 256, 512, generator=g) * 255, torch.rand(1, 3, 256, 512, generator=g) * 255)

    # ---- inference and metric

    def _load_pair(self, case: Case, device: str):
        torch = self._torch
        if isinstance(case.inputs, tuple) and hasattr(case.inputs[0], "shape"):
            im1, im2 = case.inputs
            return im1.to(device), im2.to(device)
        import numpy as np
        img1 = np.array(self._frame_utils.read_gen(case.inputs[0])).astype(np.uint8)
        img2 = np.array(self._frame_utils.read_gen(case.inputs[1])).astype(np.uint8)
        if img1.ndim == 2:
            img1 = np.tile(img1[..., None], (1, 1, 3)); img2 = np.tile(img2[..., None], (1, 1, 3))
        img1, img2 = img1[..., :3], img2[..., :3]
        t1 = torch.from_numpy(img1).permute(2, 0, 1).float()[None].to(device)
        t2 = torch.from_numpy(img2).permute(2, 0, 1).float()[None].to(device)
        return t1, t2

    def infer(self, model: Any, case: Case, rec: TrajectoryRecorder) -> Prediction:
        self._import()
        torch = self._torch
        device = next(model.parameters()).device
        im1, im2 = self._load_pair(case, device)
        padder = self._utils.InputPadder(im1.shape, mode="kitti")
        im1, im2 = padder.pad(im1, im2)
        with torch.no_grad(), rec.attached(model.update_block, output_index=2):
            _, flow_up = model(im1, im2, iters=self.iterations, test_mode=True)
        flow = padder.unpad(flow_up[0]).detach().cpu()
        return Prediction(output=flow)

    def metric_value(self, pred: Prediction, case: Case) -> Optional[float]:
        if case.gt is None:
            return None
        self._import()
        torch = self._torch
        import numpy as np
        flow_gt, valid = self._frame_utils.readFlowKITTI(case.gt)
        flow_gt = torch.from_numpy(np.array(flow_gt).astype(np.float32)).permute(2, 0, 1)
        valid = torch.from_numpy(np.array(valid)).float()
        epe = torch.sum((pred.output - flow_gt) ** 2, dim=0).sqrt().view(-1)
        val = valid.view(-1) >= 0.5
        return float(epe[val].mean().item())

    def expected_iterations(self) -> Optional[int]:
        return self.iterations


def is_small_state_dict(state: dict) -> bool:
    """raft-small has no learned upsampling mask; full RAFT has `update_block.mask.*`."""
    return not any(k.startswith("update_block.mask.") for k in state)


def git_sha(path: Path) -> Optional[dict]:
    try:
        sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return None
        dirty = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
        return {"sha": sha.stdout.strip(), "dirty": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return None
