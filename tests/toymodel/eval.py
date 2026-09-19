"""Our evaluation: mean endpoint error in pixels over all pixels, per sample and averaged. Usage: python eval.py ckpt.pt data/val"""
import sys, glob, os
import numpy as np
import torch
from model import IterFlow


def load(ckpt, device="cpu"):
    m = IterFlow()
    m.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True))
    return m.to(device).eval()


def sample(path):
    d = np.load(path)
    x = torch.from_numpy(np.concatenate([d["frame1"], d["frame2"]], axis=0)).float()[None] / 255.0   # (1, 6, H, W)
    gt = torch.from_numpy(d["field"]).float()                                                        # (2, H, W)
    return x, gt


def epe(pred, gt):
    return torch.sqrt(((pred - gt) ** 2).sum(0)).mean().item()


if __name__ == "__main__":
    model = load(sys.argv[1])
    errs = []
    for p in sorted(glob.glob(os.path.join(sys.argv[2], "*.npz"))):
        x, gt = sample(p)
        with torch.no_grad():
            out, _ = model(x, iters=8)
        e = epe(out[0], gt)
        errs.append(e)
        print(f"{os.path.basename(p)[:-4]}\t{e:.4f}")
    print(f"mean\t{np.mean(errs):.4f}")
