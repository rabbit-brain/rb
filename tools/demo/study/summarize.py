"""Prints one line per finished run: every results/*/metrics.json, with the settings from its config.yaml.

    python summarize.py"""
import json

from common import ROOT, load_yaml


def main():
    print(f"{'run':<28} {'seed':>4} {'scheme':<11} {'calib':>5} {'normalize mean':<22} {'fp32':>6} {'int8':>6}")
    for metrics_file in sorted((ROOT / "results").glob("r*_*/metrics.json")):
        folder = metrics_file.parent
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        config = load_yaml(folder / "config.yaml")
        quant = config.get("quant") or {}
        int8 = metrics.get("int8", {}).get("top1")
        print(f"{folder.name:<28} {config['seed']:>4} {quant.get('scheme', '-'):<11} "
              f"{quant.get('calibration', {}).get('images', '-'):>5} {str(config['data']['normalize']['mean']):<22} "
              f"{metrics['fp32']['top1']:>6.2f} {'-' if int8 is None else f'{int8:.2f}':>6}")
    crashed = [p.name for p in sorted((ROOT / "results").glob("r*_*")) if p.is_dir() and not (p / "metrics.json").exists()]
    if crashed:
        print("no metrics.json (did not finish): " + ", ".join(crashed))


if __name__ == "__main__":
    main()
