#!/usr/bin/env bash
# Rabbit Brain on real weights: princeton-vl/RAFT checkpoints on KITTI-2015 (training set, 200 pairs, ground truth).
# Written for a fresh GPU pod (RunPod "PyTorch" template or any image with torch + CUDA), run as root:
#   curl -sL https://raw.githubusercontent.com/rabbit-brain/rb/main/examples/raft-kitti/runpod.sh | bash
# Everything lands in $W (default /workspace/rb-kitti). Results: $W/rb-runs/<run_id>/{report.md,findings.json,bundle.json,record.json}.
# At the end a file server on port 8000 serves $W (expose the port on the pod to read results from outside).
#
# Data notes. raft-kitti was fine-tuned on this same training set, so its errors are in-sample: it shows what a
# candidate that has seen the cases looks like. raft-sintel is a candidate fine-tuned elsewhere. raft-small is a
# smaller architecture (detected from the checkpoint). Baseline for all three: raft-things.
set -euo pipefail
W="${W:-/workspace/rb-kitti}"
ITERS="${ITERS:-12}"
mkdir -p "$W" && cd "$W"
exec > >(tee -a run.log) 2>&1
echo "== $(date -u +%FT%TZ) rabbit-brain on RAFT / KITTI-2015 in $W"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || echo "no nvidia-smi"

for t in git unzip curl; do
  command -v "$t" >/dev/null || { apt-get update -qq && apt-get install -y -qq "$t"; }
done

echo "== tool (runner is on main; PyPI 0.1.x has no rb run)"
pip install -q "rabbit-brain[raft] @ https://github.com/rabbit-brain/rb/archive/refs/heads/main.tar.gz"
rb version

echo "== RAFT code"
if [ ! -d raft/core ]; then
  git clone -q https://github.com/princeton-vl/RAFT.git raft
fi
git -C raft rev-parse --short HEAD

echo "== RAFT checkpoints (models.zip from the RAFT README's download_models.sh)"
if [ ! -f raft/models/raft-things.pth ]; then
  curl -sSL -o models.zip https://dl.dropboxusercontent.com/s/4j4z58wuv8o0mfz/models.zip
  unzip -tq models.zip >/dev/null || { echo "models.zip is not a zip (Dropbox link changed?). Put raft-things.pth, raft-sintel.pth, raft-kitti.pth, raft-small.pth under $W/raft/models/ and rerun."; exit 3; }
  unzip -q -o models.zip -d raft && rm -f models.zip
fi
ls raft/models

echo "== KITTI-2015 scene flow (training: image_2 + flow_occ), about 1.7 GB"
if [ ! -d data/kitti2015/training/image_2 ]; then
  curl -sSL -o data_scene_flow.zip https://s3.eu-central-1.amazonaws.com/avg-kitti/data_scene_flow.zip
  unzip -tq data_scene_flow.zip >/dev/null || { echo "data_scene_flow.zip is not a zip. Download it from https://www.cvlibs.net/datasets/kitti/eval_scene_flow.php and unzip into $W/data/kitti2015/"; exit 3; }
  mkdir -p data/kitti2015 && unzip -q -o data_scene_flow.zip -d data/kitti2015 && rm -f data_scene_flow.zip
fi
echo "pairs: $(ls data/kitti2015/training/image_2/*_10.png | wc -l), ground truth: $(ls data/kitti2015/training/flow_occ/*_10.png | wc -l)"

echo "== project"
rb init --project raft-kitti --adapter raft --model-code ./raft --dataset ./data/kitti2015/training --dataset-name kitti2015-train --iterations "$ITERS" --device cuda --force
rb doctor --checkpoint raft/models/raft-things.pth --checkpoint raft/models/raft-sintel.pth --checkpoint raft/models/raft-kitti.pth --checkpoint raft/models/raft-small.pth
rb verify-hook --checkpoint raft/models/raft-things.pth

for cand in raft-sintel raft-kitti raft-small; do
  echo "== run: raft-things -> $cand ($ITERS iterations)"
  time rb run --baseline raft/models/raft-things.pth --candidate "raft/models/$cand.pth" --quiet
done

echo "== runs"
rb runs
for d in rb-runs/*/; do
  echo "--- $d"
  rb findings "$d" --top 5
done

echo "== serving $W on port 8000 (results under rb-runs/)"
tar -czf rb-kitti-runs.tar.gz rb-runs rb.toml run.log
nohup python3 -m http.server 8000 --bind 0.0.0.0 >/dev/null 2>&1 &
echo "== done $(date -u +%FT%TZ)"
