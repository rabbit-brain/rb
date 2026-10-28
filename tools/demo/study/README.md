# int8-cifar

Does INT8 post-training quantization keep a ResNet-20 within 1 point of FP32 top-1 on CIFAR-10?

Two smaller questions on the way:

- How many calibration images does INT8 need?
- Do per-channel weight scales matter, against one scale per tensor?

Reference: ResNet-20 reaches 91.25% top-1 on CIFAR-10 (He et al. 2016, "Deep Residual Learning for Image Recognition", table 6, 8.75% error).

Everything runs on CPU: PyTorch with the fbgemm backend, eager-mode post-training static quantization.

## Setup

```
pip install -r requirements.txt
```

CIFAR-10 goes in `data/` as torchvision's `cifar-10-batches-py`.

## Train

```
python train.py --seed 0 --out results/<run>
python eval.py results/<run>
```

Settings are in `configs/train.yaml`, `configs/data.yaml` and `configs/eval.yaml`. Each run folder keeps the config it resolved (`config.yaml`), its log and `metrics.json`; checkpoints stay out of git.

## Quantize

```
python quantize.py --from results/<fp32 run> --scheme per_tensor --calib-images 512 --out results/<run>
```

Settings are in `configs/quant.yaml`. The run evaluates the FP32 model it starts from and the INT8 model, on the same test images.

## Sweep

```
python sweep.py configs/sweep.yaml --jobs 4 --threads 2
python summarize.py
```

Each cell of the grid is one run; `results/runs.db` (SQLite, table `runs`) has a row per run with its status and top-1.
