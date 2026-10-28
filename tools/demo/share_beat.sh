#!/bin/sh
# The share beat on the Mac (tools/demo/share_beat.mjs says what it films):
#
#   tools/demo/share_beat.sh <rb binary> <record folder> <share link> <out dir> ['<the command that records the new run>'] [share_beat.mjs options]
#
# The record folder is the share kit (the demo record with one more real run's folder, not yet recorded); the run's
# command defaults to recording that folder. rb sync --watch uses the credential `rb login` keeps on this machine.
# Needs node 22, ffmpeg, Google Chrome and a python3 with Pillow (one is made in ~/.cache/rb-demo-venv if there is none).
set -eu
if [ $# -lt 4 ]; then
  sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
here=$(cd "$(dirname "$0")" && pwd)
rb=$1 record=$2 link=$3 out=$4
run=${5:-"r=results/r032_int8_pc_c128_s1; rb run add e-ptq --from \$r/metrics.json --config \$r/config.yaml --set seed=1 --set scheme=per_channel --set calib_images=128 --set calib_seed=0"}
if [ $# -ge 5 ]; then shift 5; else shift 4; fi
for tool in node ffmpeg; do
  command -v "$tool" >/dev/null || { echo "share_beat.sh: needs $tool" >&2; exit 2; }
done
py=python3
if ! "$py" -c 'import PIL' 2>/dev/null; then
  py="$HOME/.cache/rb-demo-venv/bin/python"
  [ -x "$py" ] || { python3 -m venv "$HOME/.cache/rb-demo-venv" && "$py" -m pip install -q pillow; }
fi
exec node "$here/share_beat.mjs" --rb "$rb" --record "$record" --link "$link" --out "$out" --run "$run" --python "$py" "$@"
