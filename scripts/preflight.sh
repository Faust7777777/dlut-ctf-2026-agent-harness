#!/usr/bin/env bash
# Pre-contest readiness check.  Run before each major step.
# Exits non-zero if any P0 check fails so a wrapper can refuse to start.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

source tools/env.sh 2>/dev/null || true

ok=0
warn=0
fail=0

pass() {  printf '  [PASS] %s\n' "$1"; ok=$((ok+1)); }
warning() { printf '  [WARN] %s\n' "$1"; warn=$((warn+1)); }
failure() { printf '  [FAIL] %s\n' "$1"; fail=$((fail+1)); }

section() { printf '\n=== %s ===\n' "$1"; }

# --- 1. Workspace ---
section "Workspace"
[[ -d "$ROOT_DIR" ]] && pass "project root present" || failure "project root missing: $ROOT_DIR"
[[ -d "$ROOT_DIR/.venv" ]] && pass "venv present" || failure "venv missing"
[[ -f "$ROOT_DIR/configs/config.yaml" ]] && pass "config.yaml present" || failure "config.yaml missing"
[[ -f "$ROOT_DIR/.env" ]] && pass ".env present" || warning ".env missing (Feishu/CTF tokens unconfigured)"

# --- 2. Question bank ---
section "Question bank"
bank="$ROOT_DIR/data/processed/question_bank_merged.json"
if [[ -s "$bank" ]]; then
  total=$(python3 -c "import json; print(len(json.load(open('$bank'))['questions']))" 2>/dev/null || echo 0)
  if [[ "$total" -ge 2000 ]]; then
    pass "merged bank has $total questions"
  else
    warning "merged bank only has $total questions (<2000)"
  fi
else
  failure "merged bank missing or empty: $bank"
fi

# --- 3. Kill switch state ---
section "Kill switch"
ks="$ROOT_DIR/.auto_submit_off"
if [[ -f "$ks" ]]; then
  warning "kill switch ACTIVE — auto_submit will downgrade to human_review"
else
  pass "kill switch inactive (auto_submit allowed when other gates pass)"
fi

# --- 4. Submission state ---
section "Submission state"
state="$ROOT_DIR/logs/submission_state.json"
if [[ -f "$state" ]]; then
  if python3 -c "import json; json.load(open('$state'))" 2>/dev/null; then
    pass "state.json parseable"
  else
    failure "state.json corrupt — investigate before contest start"
  fi
else
  pass "no prior state.json (clean start)"
fi

# --- 5. Python deps ---
section "Python deps"
for pkg in fitz pdfplumber rapidfuzz yaml dotenv requests httpx fastapi; do
  if python3 -c "import $pkg" 2>/dev/null; then
    pass "python: $pkg"
  else
    failure "python: $pkg missing"
  fi
done

# --- 6. CTF tools (sample) ---
section "CTF tools (sample)"
for cmd in curl gdb ghidra java python3 git; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "tool: $cmd"
  else
    warning "tool: $cmd missing"
  fi
done

# --- 6.5 Node / Playwright (wjx_exam_assist needs these) ---
section "Node / Playwright"
if command -v node >/dev/null 2>&1; then
  pass "node: $(node --version)"
else
  failure "node missing — wjx_exam_assist.js cannot run"
fi
if command -v npm >/dev/null 2>&1; then
  pass "npm: $(npm --version)"
else
  warning "npm missing — cannot install/refresh Node deps"
fi
if [[ -f "$ROOT_DIR/package.json" ]]; then
  pass "package.json present"
else
  failure "package.json missing in project root"
fi
if [[ -d "$ROOT_DIR/node_modules/playwright" ]]; then
  pw_ver=$(node -e "console.log(require('$ROOT_DIR/node_modules/playwright/package.json').version)" 2>/dev/null || echo "?")
  pass "playwright (project-local): $pw_ver"
else
  failure "node_modules/playwright missing — run: npm install"
fi
# Verify chromium is downloaded (the part npx playwright install fetches).
if [[ -d "$HOME/.cache/ms-playwright" ]] && \
   ls "$HOME/.cache/ms-playwright" 2>/dev/null | grep -q '^chromium-'; then
  chromium_ver=$(ls "$HOME/.cache/ms-playwright" | grep '^chromium-' | head -1)
  pass "playwright chromium: $chromium_ver"
else
  warning "playwright chromium not cached — run: npx playwright install chromium"
fi

# --- 7. Disk space ---
section "Disk"
avail=$(df -BM "$ROOT_DIR" | awk 'NR==2 {gsub("M","",$4); print $4}')
if [[ "${avail:-0}" -lt 500 ]]; then
  warning "free space ${avail}M (<500M)"
else
  pass "free space ${avail}M"
fi

# --- 8. Time / timezone ---
section "Time"
tz=$(date +%Z)
pass "timezone $tz"
ts=$(date '+%Y-%m-%d %H:%M:%S %z')
pass "now $ts"

# --- Summary ---
section "Summary"
printf '  pass=%d  warn=%d  fail=%d\n' "$ok" "$warn" "$fail"
[[ "$fail" -eq 0 ]] && exit 0
exit 1
