#!/usr/bin/env bash
# One-shot validation runner. Use this before 5/9 dress rehearsal and at
# any point after a code change to confirm nothing has broken.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source tools/env.sh

failed=0

run_step() {
  local label="$1"
  shift
  printf '\n=== %s ===\n' "$label"
  if "$@"; then
    printf '  [PASS] %s\n' "$label"
  else
    printf '  [FAIL] %s\n' "$label"
    failed=$((failed+1))
  fi
}

run_step "preflight" bash scripts/preflight.sh

run_step "unit: lookup_guard" python -m unittest tests.test_lookup_guard
run_step "unit: bank_fixes" python -m unittest tests.test_bank_fixes
run_step "unit: option_anomaly" python -m unittest tests.test_option_anomaly
run_step "unit: flag_guard" python -m unittest tests.test_flag_guard
run_step "unit: notifications" python -m unittest tests.test_notifications
run_step "unit: skill_workflow" python -m unittest tests.test_skill_workflow
run_step "unit: lookup_service (HTTP)" python -m unittest tests.test_lookup_service
run_step "unit: paper_manifest" python -m unittest tests.test_paper_manifest
run_step "unit: wjx_assist_e2e_rejection" python -m unittest tests.test_wjx_assist_e2e_rejection
run_step "unit: gzctf_adapter" python -m unittest tests.test_gzctf_adapter
run_step "unit: ai_contest_supervisor" python -m unittest tests.test_ai_contest_supervisor
run_step "unit: route_control" python -m unittest tests.test_route_control
run_step "unit: ai_contest_example_config_smoke" python -m unittest tests.test_ai_contest_example_config_smoke
run_step "unit: run_real_llm_solve" python -m unittest tests.test_run_real_llm_solve
run_step "unit: codex_sidecar" python -m unittest tests.test_codex_sidecar
run_step "unit: sage_env_superset" python -m unittest tests.test_sage_env_superset
run_step "unit: runtime_preflight" python -m unittest tests.test_runtime_preflight

if command -v node >/dev/null 2>&1; then
  run_step "unit: wjx_assist_logic (node)" node tests/test_wjx_assist_logic.js
else
  printf '\n=== unit: wjx_assist_logic (node) ===\n  [SKIP] node not found in PATH\n'
fi

run_step "scan: option_anomaly_scan" python scripts/option_anomaly_scan.py
run_step "smoke: lookup_engine" python scripts/smoke_test_lookup.py
run_step "dryrun: skill_workflow" python scripts/skill_workflow_dryrun.py
run_step "rehearsal: 5_9 scenarios" python scripts/rehearsal_5_9.py
run_step "rehearsal: ai_identity" python scripts/rehearsal_ai_identity.py
run_step "dryrun: codex_sidecar" python scripts/codex_sidecar_dryrun.py
run_step "preflight: runtime capabilities" python scripts/runtime_preflight.py

# Optional: real BJDCTF run only when fixtures are present locally
if [[ -d "$ROOT_DIR/data/external_ctf/bjdctf2020-misc" ]]; then
  run_step "realctf: skill_workflow_realctf" python scripts/skill_workflow_realctf.py
else
  printf '\n=== realctf: skill_workflow_realctf ===\n  [SKIP] data/external_ctf/bjdctf2020-misc not present\n'
fi

# Notification message previews (no webhook needed)
run_step "preview: notification templates" python -c "
from ctf_agents.submit.notifications import preview_message
print('--- freeze ---')
print(preview_message('freeze', challenge_id='web-03', category='web', wrong_count=2, max_wrong=2, last_flag_redacted='flag{a…xyz}', log_hint='logs/run-X.jsonl#L42'))
print('--- human_review ---')
print(preview_message('human_review', challenge_id='web-03', category='web', score=0.87, flag_redacted='flag{a…xyz}', reason='证据不足'))
print('--- kill_switch on ---')
print(preview_message('kill_switch', activated=True, reason='平台异常'))
print('--- force_submit ---')
print(preview_message('force_submit', challenge_id='web-03', flag_redacted='flag{a…xyz}', correct=True, reason='browser confirmed', actor='human:cli'))
"

printf '\n========================================\n'
if [[ "$failed" -eq 0 ]]; then
  printf '  ALL CHECKS PASSED\n'
  exit 0
else
  printf '  %d STEP(S) FAILED\n' "$failed"
  exit 1
fi
