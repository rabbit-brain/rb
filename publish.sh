#!/usr/bin/env bash
# Push main to github.com/rabbit-brain/rb and upload the release in dist/ to PyPI.
# Run from anywhere: bash ~/Desktop/RB/product/rabbit-brain/publish.sh
# Needs: git with your GitHub credentials (SSH key or credential helper), python3, and a PyPI API token
# (twine asks for it; use __token__ as the username). Tools go in a temporary venv; nothing is stored.
set -euo pipefail
cd "$(dirname "$0")"
ver="${1:-0.1.1}"

echo "== git"
if [ ! -d .git ]; then
  git init -b main
fi
git add -A
if ! git diff --cached --quiet; then
  git commit -m "rabbit-brain $ver: README and metadata"
fi
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin git@github.com:rabbit-brain/rb.git
fi
git push -u origin main

echo "== PyPI ($ver artifacts in dist/)"
tmp="$(mktemp -d)"
python3 -m venv "$tmp/tools"
"$tmp/tools/bin/pip" install --quiet --upgrade twine
"$tmp/tools/bin/twine" check dist/rabbit_brain-"$ver"*
"$tmp/tools/bin/twine" upload --skip-existing dist/rabbit_brain-"$ver"*

echo "== verify"
sleep 20
python3 -m venv "$tmp/v" && "$tmp/v/bin/pip" install --quiet --no-cache-dir "rabbit-brain==$ver" && "$tmp/v/bin/rb" version
echo "done: https://github.com/rabbit-brain/rb and https://pypi.org/project/rabbit-brain/$ver/"
