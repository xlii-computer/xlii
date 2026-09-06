#!/usr/bin/env bash
# T0 frozen-sidecar build (proposals/tauri-shell.md).
#
# Usage: lab/tauri-shell/sidecar/build.sh [scratch-dir]
#
# Creates a clean venv with HEADLESS CORE xlii (no extras) + PyInstaller,
# freezes via xlii-sidecar.spec, and leaves the bundle at
# <scratch>/dist/xlii-sidecar/. Nothing is written inside the repo.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
SCRATCH="${1:-$(mktemp -d)}"
VENV="$SCRATCH/venv"

echo "==> scratch: $SCRATCH"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$REPO_ROOT" pyinstaller

cd "$SCRATCH"
"$VENV/bin/pyinstaller" --clean --noconfirm \
  --distpath "$SCRATCH/dist" --workpath "$SCRATCH/build" \
  "$HERE/xlii-sidecar.spec"

BIN="$SCRATCH/dist/xlii-sidecar/xlii-sidecar"
echo "==> built: $BIN"
du -sh "$SCRATCH/dist/xlii-sidecar"
"$BIN" --version
