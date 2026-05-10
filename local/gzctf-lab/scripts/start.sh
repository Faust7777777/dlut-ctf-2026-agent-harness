#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p "$ROOT_DIR/data/db" "$ROOT_DIR/data/files"

docker compose -f "$ROOT_DIR/compose.yml" up -d
python3 "$ROOT_DIR/scripts/lab.py" wait
printf 'local GZCTF is reachable at http://127.0.0.1:8080\n'
