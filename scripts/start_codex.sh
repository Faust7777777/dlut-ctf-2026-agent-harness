#!/usr/bin/env bash
set -euo pipefail
PROMPT_FILE="${1:-prompts/coordinator.md}"
if ! command -v codex >/dev/null 2>&1; then echo "[WARN] codex CLI 未安装或不在 PATH。下面是应复制给 Codex 的提示词："; cat "$PROMPT_FILE"; exit 0; fi
export RUN_ID="${RUN_ID:-run-$(date +%Y%m%d-%H%M%S)}"
cat "$PROMPT_FILE" | codex
