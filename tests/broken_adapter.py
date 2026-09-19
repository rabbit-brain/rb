"""A RAFT adapter with the paper's bug class: the encoder gets the wrong input range.

Used by the tests only. RAFT's forward normalises 0..255 images to [-1, 1] itself; this adapter feeds 0..1 images, so
every shape is right, every number is finite and plausible, and none of them is about the model.
"""
from rabbit_brain.adapters.raft import RaftAdapter


class HalfRangeRaft(RaftAdapter):
    def _load_pair(self, case, device):
        t1, t2 = super()._load_pair(case, device)
        return t1 / 255.0, t2 / 255.0
