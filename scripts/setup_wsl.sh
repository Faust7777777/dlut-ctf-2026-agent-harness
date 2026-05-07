#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 1. System packages.
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git jq curl unzip p7zip-full file binutils build-essential gdb exiftool binwalk tshark ripgrep

# 2. Python venv + dependencies (httpx is required by tests/test_lookup_service.py).
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Project-level Node dependencies for the Wenjuanxing assist.  The
#    chromium binary is large (~150MB) and is cached under
#    ~/.cache/ms-playwright; it's only re-downloaded when the playwright
#    package version in package.json changes.
if command -v npm >/dev/null 2>&1; then
  npm install --no-audit --no-fund
  npx --yes playwright install chromium
else
  printf '[WARN] npm not found; skipped Node setup. Install Node >=18, then run:\n'
  printf '       cd %s && npm install && npx playwright install chromium\n' "$(pwd)"
fi

mkdir -p data/raw data/processed logs writeups workspace
printf '[OK] WSL base setup finished. Fill .env and configs/config.yaml next.\n'
