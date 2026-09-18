"""Record a refinement trajectory inside your model's update loop. Label-free: no ground truth needed.

Two ways to record, both giving one number per iteration, the mean |update| per pixel over the valid region:

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

Works with torch tensors, numpy arrays and nested lists.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional


def _flatten(value: Any) -> Iterator[float]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield float(value)


class TrajectoryRecorder:
    def __init__(self, mask: Optional[Any] = None) -> None:
        self.values: list[float] = []
        self.mask = mask  # optional boolean tensor/array of valid pixels (same spatial shape as the update)

    def step(self, delta: Any) -> float:
        if hasattr(delta, "detach"):  # torch tensor, e.g. shape (B, 2, H, W)
            magnitude = delta.detach().abs()
            if magnitude.dim() >= 3 and magnitude.shape[-3] == 2:
                magnitude = magnitude.sum(dim=-3)  # L1 norm of the 2-D update per pixel
            if self.mask is not None:
                magnitude = magnitude[self.mask]
            value = float(magnitude.float().mean().item())
        elif hasattr(delta, "mean") and hasattr(delta, "shape"):  # numpy array
            magnitude = abs(delta)
            if self.mask is not None:
                magnitude = magnitude[self.mask]
            value = float(magnitude.mean())
        else:  # plain nested lists
            flat = [abs(v) for v in _flatten(delta)]
            value = sum(flat) / max(len(flat), 1)
        self.values.append(round(value, 4))
        return value

    def reset(self) -> None:
        self.values = []

    def __len__(self) -> int:
        return len(self.values)

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
