"""IterFlow: a tiny iterative refinement model for 2-channel dense fields (a stand-in for our production estimator).

forward(x, iters) starts from a zero field and applies the update block `iters` times; each call returns the field
after every iteration so callers can inspect the refinement. Inputs: x of shape (B, 6, H, W) = two RGB frames
scaled to [0, 1]. Output: (B, 2, H, W) field, in pixels.
"""
import torch
import torch.nn as nn


class UpdateBlock(nn.Module):
    def __init__(self, hidden=16):
        super().__init__()
        self.net = nn.Sequential(nn.Conv2d(6 + 2, hidden, 3, padding=1), nn.ReLU(), nn.Conv2d(hidden, hidden, 3, padding=1), nn.ReLU(), nn.Conv2d(hidden, 2, 3, padding=1))
        self.gain = nn.Parameter(torch.tensor(0.5))

    def forward(self, x, field):
        delta = self.net(torch.cat([x, field], dim=1)) * self.gain
        return delta


class IterFlow(nn.Module):
    def __init__(self, hidden=16):
        super().__init__()
        self.update = UpdateBlock(hidden)

    def forward(self, x, iters=8):
        field = torch.zeros(x.shape[0], 2, x.shape[2], x.shape[3], device=x.device)
        history = []
        for _ in range(iters):
            delta = self.update(x, field)
            field = field + delta
            history.append(field)
        return field, history
