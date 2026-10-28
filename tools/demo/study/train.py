"""Trains the FP32 ResNet-20 on CIFAR-10 and evaluates it on the test split.

    python train.py --seed 0 --out results/r004_fp32_s0

Reads configs/train.yaml, configs/data.yaml and configs/eval.yaml; writes config.yaml (as resolved, at the start),
train.log, model.pt and metrics.json into --out."""
import argparse
import math
import time

import torch
from torch import nn

from common import Log, command_line, dump_json, dump_yaml, file_kb, git_state, load_yaml, now, run_folder, set_threads
from data import batches, load_split
from eval import evaluate
from models import ResNet20, parameter_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, help="overrides configs/train.yaml")
    args = ap.parse_args()

    train_cfg, data_cfg, eval_cfg = load_yaml("configs/train.yaml"), load_yaml("configs/data.yaml"), load_yaml("configs/eval.yaml")
    if args.epochs is not None:
        train_cfg["epochs"] = args.epochs
    out = run_folder(args.out)
    config = {"run_id": out.name, "kind": "train", "seed": args.seed, "command": command_line(), "started_at": now(),
              "git": git_state(), "train": train_cfg, "data": data_cfg, "eval": eval_cfg}
    dump_yaml(config, out / "config.yaml")
    log = Log(out / "train.log")
    threads = set_threads()

    torch.manual_seed(args.seed)
    g = torch.Generator().manual_seed(args.seed)
    x_train, y_train = load_split(data_cfg, "train")
    x_test, y_test = load_split(data_cfg, "test")
    model = ResNet20().to(memory_format=torch.channels_last)     # about a third faster on CPU than NCHW
    log(f"{out.name}: resnet20, {parameter_count(model)} parameters, seed {args.seed}, {threads} threads")
    log(f"data: {data_cfg['dataset']}, {len(x_train)} train / {len(x_test)} test, "
        f"normalize mean {data_cfg['normalize']['mean']} std {data_cfg['normalize']['std']}")

    opt_cfg = train_cfg["optimizer"]
    opt = torch.optim.SGD(model.parameters(), lr=opt_cfg["lr"], momentum=opt_cfg["momentum"],
                          weight_decay=opt_cfg["weight_decay"], nesterov=opt_cfg["nesterov"])
    epochs, bs = train_cfg["epochs"], train_cfg["batch_size"]
    steps_per_epoch = math.ceil(len(x_train) / bs)
    total = epochs * steps_per_epoch
    loss_fn = nn.CrossEntropyLoss()
    step = 0
    t0 = time.time()
    for epoch in range(1, epochs + 1):
        model.train()
        te, seen, correct, loss_sum = time.time(), 0, 0, 0.0
        for i, (xb, yb) in enumerate(batches(x_train, y_train, bs, data_cfg, g, train=True), 1):
            lr = 0.5 * opt_cfg["lr"] * (1 + math.cos(math.pi * step / total))
            for group in opt.param_groups:
                group["lr"] = lr
            logits = model(xb.contiguous(memory_format=torch.channels_last))
            loss = loss_fn(logits, yb)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            step += 1
            seen += len(yb)
            correct += (logits.argmax(1) == yb).sum().item()
            loss_sum += loss.item() * len(yb)
            if i % train_cfg["log_every"] == 0:
                log(f"epoch {epoch}/{epochs} step {i}/{steps_per_epoch} loss {loss.item():.4f} lr {lr:.5f}")
        log(f"epoch {epoch}/{epochs} done: train_loss {loss_sum / seen:.4f} train_top1 {100 * correct / seen:.2f} "
            f"({time.time() - te:.1f} s)")

    torch.save(model.state_dict(), out / "model.pt")
    result = evaluate(model, x_test, y_test, data_cfg, eval_cfg["batch_size"])
    log(f"test top1 {result['top1']:.2f} top5 {result['top5']:.2f} ({result['n']} images)")
    metrics = {"run_id": out.name, "fp32": {"top1": result["top1"], "top5": result["top5"], "size_kb": file_kb(out / "model.pt")},
               "train_loss": round(loss_sum / seen, 4), "train_top1": round(100 * correct / seen, 2),
               "epochs": epochs, "wall_s": round(time.time() - t0, 1), "finished_at": now()}
    dump_json(metrics, out / "metrics.json")
    log(f"wrote {out / 'metrics.json'}")


if __name__ == "__main__":
    main()
