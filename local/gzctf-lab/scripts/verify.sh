#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/../.." && pwd)"

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

export GZCTF_ADMIN_PASSWORD="${GZCTF_ADMIN_PASSWORD:-LocalGZCTFAdmin2026!}"
export GZCTF_PLAYER_USERNAME="${GZCTF_PLAYER_USERNAME:-player}"
export GZCTF_PLAYER_PASSWORD="${GZCTF_PLAYER_PASSWORD:-LocalGZCTFPlayer2026!}"
export GZCTF_PLAYER_EMAIL="${GZCTF_PLAYER_EMAIL:-player@example.invalid}"
log_dir="$REPO_ROOT/logs/local-gzctf"

bash "$ROOT_DIR/scripts/reset.sh"
bash "$ROOT_DIR/scripts/start.sh"
bash "$ROOT_DIR/scripts/seed.sh"

python -m unittest tests.test_gzctf_adapter
python -m unittest tests.test_ai_contest_supervisor tests.test_ai_contest_example_config_smoke

python3 "$ROOT_DIR/scripts/lab.py" verify

state_dir="$REPO_ROOT/state/local-gzctf"
mkdir -p "$state_dir" "$log_dir"

python scripts/ai_contest_supervisor.py --config configs/ai_contest.local.example.yaml --healthcheck-only

if ! rg -n 'healthcheck_ok' "$log_dir"/*.jsonl; then
  echo "healthcheck_ok not found in supervisor logs" >&2
  exit 1
fi

timeout 90s python scripts/ai_contest_supervisor.py --config configs/ai_contest.local.example.yaml || true
timeout 60s python scripts/ai_contest_supervisor.py --config configs/ai_contest.local.example.yaml || true

dup_config="$(mktemp)"
cp configs/ai_contest.local.example.yaml "$dup_config"
python3 - "$dup_config" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
text = p.read_text()
text = text.replace("min_conf_auto_submit: 0.60", "min_conf_auto_submit: 0.63")
text = text.replace("min_conf_human_review: 0.55", "min_conf_human_review: 0.60")
text = text.replace("min_seconds_between_submits_global: 1", "min_seconds_between_submits_global: 0")
text = text.replace("min_seconds_between_submits_per_challenge: 1", "min_seconds_between_submits_per_challenge: 0")
text = text.replace("state/local-gzctf", "state/local-gzctf-dup")
text = text.replace("artifacts/local-gzctf", "artifacts/local-gzctf-dup")
text = text.replace("logs/local-gzctf", "logs/local-gzctf-dup")
text = text.replace(
    '  enabled_categories:\n    - "misc"\n    - "forensics"\n    - "crypto"\n',
    '  enabled_categories:\n    - "misc"\n    - "forensics"\n    - "crypto"\n    - "web"\n',
)
text = text.replace(
    '  auto_submit_categories:\n    - "misc"\n    - "forensics"\n    - "crypto"\n',
    '  auto_submit_categories:\n    - "misc"\n    - "forensics"\n    - "crypto"\n    - "web"\n',
)
p.write_text(text)
PY
mkdir -p "$REPO_ROOT/state/local-gzctf-dup" "$REPO_ROOT/logs/local-gzctf-dup" "$REPO_ROOT/artifacts/local-gzctf-dup"
timeout 60s python scripts/ai_contest_supervisor.py --config "$dup_config" || true
timeout 30s python scripts/ai_contest_supervisor.py --config "$dup_config" || true

if ! rg -n '"event_type": ?"heartbeat"' "$log_dir"/*.jsonl; then
  echo "missing heartbeat evidence" >&2
  exit 1
fi
if ! rg -n '"status": ?"Accepted"|Accepted' "$log_dir"/*.jsonl; then
  echo "missing Accepted evidence" >&2
  exit 1
fi
if ! rg -n '"status": ?"WrongAnswer"|WrongAnswer' "$log_dir"/*.jsonl; then
  echo "missing WrongAnswer evidence" >&2
  exit 1
fi
if rg -n '"event_type": ?"duplicate_candidate_skipped"' "$REPO_ROOT/logs/local-gzctf-dup"/*.jsonl; then
  echo "duplicate_candidate_skipped evidence came from live local GZCTF"
else
  echo "duplicate_candidate_skipped remains mock-only; live local GZCTF did not emit it stably"
fi

echo "Supervisor log tail:"
tail -n 50 "$log_dir"/*.jsonl 2>/dev/null || true

echo "Local GZCTF verification complete."
