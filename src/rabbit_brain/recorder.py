"""Record a refinement trajectory inside your model's update loop. Label-free: no ground truth needed.

Two ways to record, both giving one number per iteration, the mean update magnitude per pixel (L2 norm of the
per-pixel update vector, averaged over the valid region):

    from rabbit_brain import TrajectoryRecorder
    rec = TrajectoryRecorder()
    for k in range(iters):
        delta_flow = update_block(...)         # the existing update
        flow = flow + delta_flow
        rec.step(delta_flow)                   # (1) the one line inside the loop
    trajectory = rec.values

    rec = TrajectoryRecorder()
    with rec.attached(model.update_block, output_index=2):   # (2) no code change: a forward hook on the update module
        model(image1, image2, iters=12, test_mode=True)      #     RAFT's update_block returns (net, mask, delta_flow)
    trajectory = rec.values

Works with torch tensors, numpy arrays and nested lists. With tensors or arrays the recorder also keeps the update
fields for the case and `convergence()` computes the paper's per-case statistics from them: direction reversals
between consecutive updates (cosine < 0), the distance of every intermediate estimate from the final one, and the
update energy. Call `reset()` between cases.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Any, Iterator, Optional


def _flatten(value: Any) -> Iterator[float]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield float(value)


class TrajectoryRecorder:
    def __init__(self, mask: Optional[Any] = None, keep_fields: bool = True, scale: float = 1.0) -> None:
        self.values: list[float] = []
        self.mask = mask  # optional boolean tensor/array of valid pixels (same spatial shape as the update)
        self.keep_fields = keep_fields
        self.scale = float(scale)  # multiplies every update: 8 for RAFT, whose update fields are at 1/8 resolution, so values are image pixels
        self._fields: list[Any] = []  # per-iteration update fields, shape (..., 2, H, W) or (..., H, W), same backend as given

    def step(self, delta: Any) -> float:
        if hasattr(delta, "detach"):  # torch tensor, e.g. shape (B, 2, H, W)
            d = delta.detach().float() * self.scale
            magnitude = d.abs()
            if magnitude.dim() >= 3 and magnitude.shape[-3] == 2:
                magnitude = (magnitude ** 2).sum(dim=-3).sqrt()  # L2 norm of the 2-D update per pixel
            if self.mask is not None:
                magnitude = magnitude[self.mask]
            value = float(magnitude.mean().item())
            if self.keep_fields:
                self._fields.append(d)
        elif hasattr(delta, "mean") and hasattr(delta, "shape"):  # numpy array
            import numpy as np
            d = np.asarray(delta, dtype=np.float64) * self.scale
            magnitude = np.abs(d)
            if magnitude.ndim >= 3 and magnitude.shape[-3] == 2:
                magnitude = np.sqrt((magnitude ** 2).sum(axis=-3))
            if self.mask is not None:
                magnitude = magnitude[np.asarray(self.mask, dtype=bool)]
            value = float(magnitude.mean())
            if self.keep_fields:
                self._fields.append(d)
        else:  # plain nested lists
            flat = [abs(v) * self.scale for v in _flatten(delta)]
            value = sum(flat) / max(len(flat), 1)
        self.values.append(round(value, 4))
        return value

    def reset(self) -> None:
        self.values = []
        self._fields = []

    def __len__(self) -> int:
        return len(self.values)

    # ---- the paper's per-case convergence statistics, from the update fields

    def convergence(self) -> Optional[dict]:
        """Direction and displacement statistics averaged over pixels, or None when fewer than two fields were kept.
        Every update field must have the same shape and a leading 2-channel axis (a 2-D update per pixel)."""
        fields = self._fields
        if len(fields) < 2:
            return None
        first = fields[0]
        if getattr(first, "ndim", getattr(first, "dim", lambda: 0)()) < 3:
            return None
        if hasattr(first, "detach"):
            return self._convergence_torch(fields)
        return self._convergence_numpy(fields)

    def _convergence_torch(self, fields: list[Any]) -> Optional[dict]:
        import torch
        d = torch.stack([f.reshape(-1, 2, *f.shape[-2:])[0] if f.dim() >= 4 else f for f in fields])  # (T, 2, H, W)
        if d.shape[1] != 2:
            return None
        mask = None
        if self.mask is not None:
            mask = torch.as_tensor(self.mask, device=d.device).bool()
        norms = torch.sqrt((d ** 2).sum(dim=1))                    # (T, H, W)
        dots = (d[1:] * d[:-1]).sum(dim=1)                          # (T-1, H, W)
        cos = dots / (norms[1:] * norms[:-1]).clamp_min(1e-6)
        states = torch.cumsum(d, dim=0)                             # estimate after each iteration, from a zero start
        final = states[-1:]
        disp = torch.sqrt(((states[:-1] - final) ** 2).sum(dim=1))  # (T-1, H, W): distance of estimates 1..T-1 from the final one
        energy = (norms ** 2).sum(dim=0)                            # (H, W)

        def m(x):  # mean over valid pixels (and time when present)
            if mask is not None:
                x = x[..., mask] if x.dim() > 2 else x[mask]
            return float(x.mean().item())

        disp_t = disp.mean(dim=(1, 2)) if mask is None else disp[:, mask].mean(dim=1)
        return {
            "sign_reversal_rate": _clamp(m((cos < 0).float()), 0.0, 1.0),
            "mean_cos": _clamp(m(cos), -1.0, 1.0),
            "displacement_mean": max(0.0, m(disp)),
            "displacement_max": max(0.0, float(disp_t.max().item())),
            "displacement_initial": max(0.0, float(disp_t[0].item())),
            "update_energy": max(0.0, m(energy)),
        }

    def _convergence_numpy(self, fields: list[Any]) -> Optional[dict]:
        import numpy as np
        d = np.stack([f.reshape(-1, 2, *f.shape[-2:])[0] if f.ndim >= 4 else f for f in fields]).astype(np.float64)
        if d.shape[1] != 2:
            return None
        mask = None if self.mask is None else np.asarray(self.mask, dtype=bool)
        norms = np.sqrt((d ** 2).sum(axis=1))
        dots = (d[1:] * d[:-1]).sum(axis=1)
        cos = dots / np.maximum(norms[1:] * norms[:-1], 1e-6)
        states = np.cumsum(d, axis=0)
        disp = np.sqrt(((states[:-1] - states[-1:]) ** 2).sum(axis=1))
        energy = (norms ** 2).sum(axis=0)

        def m(x):
            if mask is not None:
                x = x[..., mask] if x.ndim > 2 else x[mask]
            return float(x.mean())

        disp_t = disp.mean(axis=(1, 2)) if mask is None else disp[:, mask].mean(axis=1)
        return {
            "sign_reversal_rate": _clamp(m((cos < 0).astype(np.float64)), 0.0, 1.0),
            "mean_cos": _clamp(m(cos), -1.0, 1.0),
            "displacement_mean": max(0.0, m(disp)),
            "displacement_max": max(0.0, float(disp_t.max())),
            "displacement_initial": max(0.0, float(disp_t[0])),
            "update_energy": max(0.0, m(energy)),
        }

    # ---- no-code-change hook for torch modules

    def attach(self, module: Any, output_index: Optional[int] = None):
        """Register a forward hook on `module` that records its output (or `output[output_index]`) every call.
        Returns the hook handle; call `.remove()` on it, or use `attached()` as a context manager."""
        def _hook(_module, _inputs, output):
            delta = output[output_index] if output_index is not None else output
            self.step(delta)
        return module.register_forward_hook(_hook)

    @contextmanager
    def attached(self, module: Any, output_index: Optional[int] = None):
        handle = self.attach(module, output_index)
        try:
            yield self
        finally:
            handle.remove()


def _clamp(v: float, lo: float, hi: float) -> float:
    if not math.isfinite(v):
        return lo
    return min(hi, max(lo, v))
