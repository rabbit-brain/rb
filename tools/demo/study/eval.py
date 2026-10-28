"""Top-1 and top-5 accuracy on the CIFAR-10 test split.

    python eval.py results/r018_int8_pt_c128_s0          # a run's saved model, with the data config it was made with
    python eval.py results/r004_fp32_s0 --data configs/data.yaml   # with the data config as it is now

Prints one JSON line: {"metrics": {"top1": ..., "top5": ...}}."""
import argparse
import json
from pathlib import Path

import torch

from common import load_yaml, set_threads
from data import batches, load_split


@torch.no_grad()
def evaluate(model, x, y, data_cfg, batch_size):
    model.eval()
    top1 = top5 = 0
    for xb, yb in batches(x, y, batch_size, data_cfg):
        logits = model(xb)
        best = logits.topk(5, dim=1).indices
        top1 += (best[:, 0] == yb).sum().item()
        top5 += (best == yb.unsqueeze(1)).any(dim=1).sum().item()
    n = len(y)
    return {"top1": round(100 * top1 / n, 2), "top5": round(100 * top5 / n, 2), "n": n}


def load_run(folder):
    """The model a run folder saved: the FP32 model of a training run, or the INT8 model of a quantize run."""
    from models import ResNet20
    folder = Path(folder)
    config = load_yaml(folder / "config.yaml")
    model = ResNet20()
    if config["kind"] == "train":
        model.load_state_dict(torch.load(folder / "model.pt", weights_only=True))
        return model, config
    from quantize import int8_skeleton
    model = int8_skeleton(model, config["quant"]["scheme"], config["quant"]["backend"])
    model.load_state_dict(torch.load(folder / "model_int8.pt", weights_only=True))
    return model, config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--data", help="a data config to use instead of the run's own")
    args = ap.parse_args()
    set_threads()
    model, config = load_run(args.run)
    data_cfg = load_yaml(args.data) if args.data else config["data"]
    x, y = load_split(data_cfg, "test")
    result = evaluate(model, x, y, data_cfg, load_yaml("configs/eval.yaml")["batch_size"])
    print(json.dumps({"metrics": {"top1": result["top1"], "top5": result["top5"]}}))


if __name__ == "__main__":
    main()
