"""Post-training static INT8 quantization of a trained FP32 run, with fbgemm, then both evaluated on the test split.

    python quantize.py --from results/r004_fp32_s0 --scheme per_tensor --calib-images 512 --out results/r005_int8_pt_c512_s0

Weights are symmetric qint8, one scale per tensor (per_tensor) or per output channel (per_channel); activations are
quint8 from a histogram observer. Calibration images are drawn from the training split without augmentation.
Reads configs/quant.yaml, configs/data.yaml and configs/eval.yaml; writes config.yaml, quantize.log, model_int8.pt
and metrics.json into --out."""
import argparse
import time
from pathlib import Path

import torch
from torch.ao.quantization import HistogramObserver, QConfig, convert, default_per_channel_weight_observer, default_weight_observer, prepare

from common import Log, command_line, dump_json, dump_yaml, file_kb, git_state, load_yaml, now, run_folder, set_threads
from data import calibration_subset, load_split, normalize
from eval import evaluate
from models import ResNet20

WEIGHT_OBSERVERS = {"per_tensor": default_weight_observer, "per_channel": default_per_channel_weight_observer}


def qconfig(scheme):
    return QConfig(activation=HistogramObserver.with_args(reduce_range=True), weight=WEIGHT_OBSERVERS[scheme])


def prepared(model, scheme, backend):
    torch.backends.quantized.engine = backend
    model.eval()
    model.fuse()
    model.qconfig = qconfig(scheme)
    return prepare(model)


def int8_skeleton(model, scheme, backend):
    """The converted module structure, for loading a saved INT8 state dict."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")            # observers that saw no data warn on convert; the state dict sets every scale
        return convert(prepared(model, scheme, backend))


@torch.no_grad()
def calibrate(model, images, data_cfg, batch_size):
    for start in range(0, len(images), batch_size):
        model(normalize(images[start:start + batch_size], data_cfg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="source", required=True, help="the FP32 training run folder")
    ap.add_argument("--scheme", choices=sorted(WEIGHT_OBSERVERS), help="overrides configs/quant.yaml")
    ap.add_argument("--calib-images", type=int, help="overrides configs/quant.yaml")
    ap.add_argument("--calib-seed", type=int, help="overrides configs/quant.yaml")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    quant_cfg, data_cfg, eval_cfg = load_yaml("configs/quant.yaml"), load_yaml("configs/data.yaml"), load_yaml("configs/eval.yaml")
    if args.scheme:
        quant_cfg["scheme"] = args.scheme
    for key, value in (("images", args.calib_images), ("subset_seed", args.calib_seed)):
        if value is not None:
            quant_cfg["calibration"][key] = value
    source = Path(args.source)
    source_cfg = load_yaml(source / "config.yaml")
    out = run_folder(args.out)
    config = {"run_id": out.name, "kind": "quantize", "from": source.as_posix(), "seed": source_cfg["seed"],
              "command": command_line(), "started_at": now(), "git": git_state(), "quant": quant_cfg, "data": data_cfg,
              "eval": eval_cfg}
    dump_yaml(config, out / "config.yaml")
    log = Log(out / "quantize.log")
    set_threads()
    t0 = time.time()

    x_train, _ = load_split(data_cfg, "train")
    x_test, y_test = load_split(data_cfg, "test")
    fp32 = ResNet20()
    fp32.load_state_dict(torch.load(source / "model.pt", weights_only=True))
    log(f"{out.name}: from {source.as_posix()} (seed {source_cfg['seed']}), {quant_cfg['backend']}, "
        f"{quant_cfg['scheme']} weights, {quant_cfg['calibration']['images']} calibration images "
        f"(subset seed {quant_cfg['calibration']['subset_seed']})")
    log(f"data: normalize mean {data_cfg['normalize']['mean']} std {data_cfg['normalize']['std']}")
    fp32_result = evaluate(fp32, x_test, y_test, data_cfg, eval_cfg["batch_size"])
    log(f"fp32 test top1 {fp32_result['top1']:.2f} top5 {fp32_result['top5']:.2f}")

    model = prepared(fp32, quant_cfg["scheme"], quant_cfg["backend"])
    images = calibration_subset(x_train, quant_cfg["calibration"]["images"], quant_cfg["calibration"]["subset_seed"])
    tc = time.time()
    calibrate(model, images, data_cfg, quant_cfg["calibration"]["batch_size"])
    log(f"calibrated on {len(images)} images ({time.time() - tc:.1f} s)")
    model = convert(model)
    torch.save(model.state_dict(), out / "model_int8.pt")
    int8_result = evaluate(model, x_test, y_test, data_cfg, eval_cfg["batch_size"])
    log(f"int8 test top1 {int8_result['top1']:.2f} top5 {int8_result['top5']:.2f}")
    log(f"top1 drop {fp32_result['top1'] - int8_result['top1']:.2f} points")
    fp32_kb = file_kb(source / "model.pt")
    metrics = {"run_id": out.name, "from": source.as_posix(),
               "fp32": {"top1": fp32_result["top1"], "top5": fp32_result["top5"], "size_kb": fp32_kb},
               "int8": {"top1": int8_result["top1"], "top5": int8_result["top5"], "size_kb": file_kb(out / "model_int8.pt")},
               "wall_s": round(time.time() - t0, 1), "finished_at": now()}
    dump_json(metrics, out / "metrics.json")
    log(f"wrote {out / 'metrics.json'}")


if __name__ == "__main__":
    main()
