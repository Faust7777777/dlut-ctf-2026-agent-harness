#!/usr/bin/env bash
set -euo pipefail
PROMPT_FILE="${1:-prompts/critic.md}"
if ! command -v claude >/dev/null 2>&1; then echo "[WARN] claude CLI 未安装或不在 PATH。下面是应复制给 Claude Code 的提示词："; cat "$PROMPT_FILE"; exit 0; fi
export RUN_ID="${RUN_ID:-run-$(date +%Y%m%d-%H%M%S)}"
claude < "$PROMPT_FILE"
