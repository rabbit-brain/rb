"""Record two channels from one RAFT inference pass: the coarse update the shipped recorder sees,
and the change in the model's own full-resolution output.

The shipped recorder hooks `model.update_block` and keeps output index 2, `delta_flow`. That field is
at 1/8 resolution, and it is only one of the two things the update block produces. The other, `up_mask`,
decides how the coarse update is spread across the 8x8 block it covers. So the amount the model's
actual output moves in an iteration is not `delta_flow` scaled by 8; it is a quantity neither field
gives alone. This module records that quantity too, by wrapping `RAFT.upsample_flow`, which the
forward loop calls once per iteration with the cumulative coarse flow and that iteration's mask, and
which returns the cumulative full-resolution flow.

Cumulative, not incremental: a trajectory is a sequence of updates, so consecutive outputs are
differenced. RAFT builds coords1 equal to coords0 and the adapter passes no `flow_init`, so the flow
before the first iteration is exactly zero. T iterations therefore give exactly T updates and there is
no starting convention to choose.

`upsample_flow` is an ordinary method on the module, not a submodule, so `register_forward_hook` cannot
reach it. It is replaced with a wrapper bound to the instance and deleted again on exit, which restores
the class method underneath.

Both channels come from the same pass. The wrapper returns the original tensor object untouched and
only copies for recording, so the model's output is unaffected; `verify_unchanged.py` checks that
bitwise rather than trusting the argument.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

from rabbit_brain.recorder import TrajectoryRecorder


class DualChannelRecorder:
    """Two TrajectoryRecorders fed from one forward pass.

    `coarse` is the shipped channel, unchanged: delta_flow at 1/8 resolution, scaled by 8 so the values
    are image pixels. `fine` is the corrected channel: the full-resolution output difference, already in
    image pixels, so scale is 1.

    Call `attached(model)` around inference, then `finish(padder)` before reading `fine`.
    """

    def __init__(self, coarse_scale: float = 8.0, keep_fields: bool = True,
                 coarse: Optional[TrajectoryRecorder] = None) -> None:
        # `coarse` may be borrowed from a caller that owns its lifetime, which is what happens under
        # `evaluate_model`: it makes the recorder, hands it to `infer`, and reads its values afterwards.
        # A borrowed recorder is never reset here; resetting it would empty the trajectory its owner is
        # about to read, and the run would report the hook as unreachable with no error anywhere.
        self._borrowed = coarse is not None
        self.coarse = coarse if coarse is not None else TrajectoryRecorder(scale=coarse_scale, keep_fields=keep_fields)
        self.fine = TrajectoryRecorder(scale=1.0, keep_fields=keep_fields)
        self._diffs: list[Any] = []       # padded full-resolution differences, in call order
        self._last_up: Optional[Any] = None   # padded cumulative output of the previous iteration
        self._final_up: Optional[Any] = None  # padded cumulative output of the last iteration
        self.calls = 0

    # ---- the two attachments

    @contextmanager
    def attached(self, model: Any, attach_coarse: bool = False):
        """Install the corrected channel for one inference. Restores the model exactly on exit.

        The coarse channel is NOT attached by default, because the adapter's own `infer` already does
        `rec.attached(model.update_block, output_index=2)` around the forward pass: pass `self.coarse`
        to `infer` as the recorder and the shipped path records it, untouched. Attaching here as well
        puts two hooks on one module and records every coarse update twice, which halves the apparent
        reversal rate. Set `attach_coarse=True` only when driving the model directly, with no adapter.
        """
        import torch

        if not hasattr(model, "upsample_flow"):
            raise RuntimeError(
                "this model has no upsample_flow; the corrected channel only exists for RAFT-family "
                "models that upsample with a learned mask"
            )
        original = type(model).upsample_flow

        def _wrapper(_self, flow, mask, __orig=original):
            up = __orig(_self, flow, mask)
            with torch.no_grad():
                cur = up.detach().to(torch.float32)          # FP32 before any subtraction
                prev = self._last_up
                diff = cur if prev is None else cur - prev   # flow before iteration 1 is exactly zero
                self._diffs.append(diff.cpu())
                self._last_up = cur
                self._final_up = cur
                self.calls += 1
            return up                                        # the model's own object, untouched

        model.upsample_flow = _wrapper.__get__(model, type(model))
        hook = self.coarse.attach(model.update_block, output_index=2) if attach_coarse else None
        try:
            yield self
        finally:
            if hook is not None:
                hook.remove()
            del model.upsample_flow                          # drops the instance attribute, class method returns

    # ---- turning the recorded outputs into a trajectory

    def finish(self, padder: Any = None, expect: Optional[int] = None) -> None:
        """Unpad the recorded differences and feed them to `fine`, in order.

        Padding is the same crop on every iteration, so cropping the differences and differencing the
        crops are the same thing; this does the cheaper one. Pass `expect` to assert the wrapper fired
        once per iteration, which it will not do if the update block produced no mask.
        """
        if expect is not None:
            if self.calls != expect:
                raise RuntimeError(
                    f"upsample_flow fired {self.calls} times for {expect} iterations. "
                    "With no up_mask RAFT falls back to upflow8 and the corrected channel is not defined."
                )
            if len(self.coarse.values) != expect:
                raise RuntimeError(
                    f"the coarse channel holds {len(self.coarse.values)} values for {expect} iterations. "
                    "A count that is an exact multiple means the update block carries more than one hook; "
                    "duplicated fields depress the reversal rate and inflate mean cosine."
                )
        for d in self._diffs:
            self.fine.step(padder.unpad(d) if padder is not None else d)

    def final_output(self, padder: Any = None):
        """The model's final full-resolution flow as the wrapper saw it, unpadded, batch dropped.
        Should equal the adapter's own `pred.output` bitwise."""
        if self._final_up is None:
            return None
        x = self._final_up.cpu()
        if padder is not None:
            x = padder.unpad(x)
        return x[0] if x.dim() == 4 else x

    def reset(self) -> None:
        """Free the recorded fields. A borrowed coarse recorder is left alone; its owner resets it."""
        if not self._borrowed:
            self.coarse.reset()
        self.fine.reset()
        self._diffs = []
        self._last_up = None
        self._final_up = None
        self.calls = 0
