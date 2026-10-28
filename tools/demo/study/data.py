"""CIFAR-10 as uint8 tensors in memory, normalized and augmented per batch (no loader workers, so a run uses only
the threads it is given). `dataset: fake` is random images for a smoke test without the download."""
from pathlib import Path

import torch

NUM_CLASSES = 10


def load_split(cfg, split):
    """(images uint8 N x 3 x 32 x 32, labels int64 N) for split "train" or "test"."""
    if cfg["dataset"] == "fake":
        n = cfg["fake"]["train_size" if split == "train" else "test_size"]
        g = torch.Generator().manual_seed(1 if split == "train" else 2)
        return torch.randint(0, 256, (n, 3, 32, 32), generator=g, dtype=torch.uint8), torch.randint(0, NUM_CLASSES, (n,), generator=g)
    if cfg["dataset"] != "cifar10":
        raise SystemExit(f"unknown dataset {cfg['dataset']!r}")
    from torchvision.datasets import CIFAR10
    ds = CIFAR10(str(Path(cfg["root"])), train=split == "train", download=cfg.get("download", False))
    return torch.from_numpy(ds.data).permute(0, 3, 1, 2).contiguous(), torch.tensor(ds.targets, dtype=torch.int64)


def normalize(x, cfg):
    mean = torch.tensor(cfg["normalize"]["mean"]).view(1, 3, 1, 1)
    std = torch.tensor(cfg["normalize"]["std"]).view(1, 3, 1, 1)
    return (x.float() / 255.0 - mean) / std


def augment(x, cfg, g):
    """Random crop with zero padding and horizontal flip, on a uint8 batch."""
    pad = cfg["augment"]["crop_padding"]
    if pad:
        n = x.shape[0]
        padded = torch.zeros((n, 3, 32 + 2 * pad, 32 + 2 * pad), dtype=x.dtype)
        padded[:, :, pad:pad + 32, pad:pad + 32] = x
        dx = torch.randint(0, 2 * pad + 1, (n,), generator=g)
        dy = torch.randint(0, 2 * pad + 1, (n,), generator=g)
        x = torch.stack([padded[i, :, dy[i]:dy[i] + 32, dx[i]:dx[i] + 32] for i in range(n)])
    if cfg["augment"]["hflip"]:
        flip = torch.rand(x.shape[0], generator=g) < 0.5
        x = torch.where(flip.view(-1, 1, 1, 1), x.flip(3), x)
    return x


def batches(x, y, batch_size, cfg, g=None, train=False):
    """Normalized batches; shuffled and augmented when train."""
    order = torch.randperm(len(x), generator=g) if train else torch.arange(len(x))
    for start in range(0, len(x), batch_size):
        idx = order[start:start + batch_size]
        xb = x[idx]
        if train:
            xb = augment(xb, cfg, g)
        yield normalize(xb, cfg), y[idx]


def calibration_subset(x, n, seed):
    """n training images, drawn without replacement by seed."""
    if n > len(x):
        raise SystemExit(f"calibration asks for {n} images; the training split has {len(x)}")
    g = torch.Generator().manual_seed(seed)
    return x[torch.randperm(len(x), generator=g)[:n]]
