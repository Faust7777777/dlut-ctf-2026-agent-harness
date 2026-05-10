#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"

export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 "$ROOT_DIR/scripts/lab.py" reset
docker compose -f "$ROOT_DIR/compose.yml" down --remove-orphans || true
rm -rf "$REPO_ROOT/state/local-gzctf" "$REPO_ROOT/logs/local-gzctf" "$REPO_ROOT/artifacts/local-gzctf" || true
printf 'local GZCTF lab reset complete\n'
