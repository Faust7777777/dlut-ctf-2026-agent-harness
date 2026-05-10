# Contest Day Runbook (AI Identity)

This is the only current contest-day AI identity source of truth for
the DLUT 5/10 skill contest. The knowledge contest may be handled by
humans / separate lookup tooling; the skill contest is the AI identity
main track.

Route-control state lives under each challenge in `state/ai_contest_state.json`; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate. The submit chain remains `supervisor -> validator -> FlagGuard -> adapter`, and helper smoke success only counts as `capability_progress`.

The AI-identity profile is fully autonomous auto-submit: misc, forensics, crypto, web, reverse, and pwn are all in `auto_submit_categories`, score thresholds are 0.0, and `pwn_reverse_force_human_review` is `false`. There is no HUMAN_REVIEW branch on the normal path — only the hard gates (format / duplicate / rate-limit / freeze / kill-switch / CheatDetected) can stop a submission.

## T-60 (12:00) Cold start

```bash
cd ~/dlut-ctf-2026
source tools/env.sh
bash scripts/preflight.sh                # 期望 29/29
ls reports/                              # 确认有 organizer screenshots, 但不分享
```

Confirm:

- WSL up, chromium GUI windows can open (only matters for the non-AI fallback)
- `.env` has `GZCTF_USERNAME`, `GZCTF_PASSWORD`, `FEISHU_WEBHOOK`
- `configs/ai_contest.yaml` exists (copy from `.example.yaml` if not)
- `state/ai_contest_state.json` does NOT exist (fresh contest)
- `.auto_submit_off` does NOT exist
- API key budgets sufficient (check Anthropic / DeepSeek dashboards)

If campus network / SSO is flaky, pre-stage both authentication paths before T-0:

- keep the live GZCTF credentials in your local, gitignored `.env`
- export a fresh browser cookie jar to `.secrets/gzctf_cookies.json`
- leave `gzctf.auth_mode` at `auto` unless the live deployment forces a single mode
- do not write the actual credential values into tracked docs, prompts, logs, or state

## T-30 (12:30) Final dry-run

```bash
# Mock rehearsal — must show all 4 paths PASS + global_disabled=True
python scripts/rehearsal_ai_identity.py
```

If this fails: **do not start skill-contest AI identity**. Use the
non-AI skill-contest fallback.

## T-15 to T-0 (12:45 – 13:00) Setup contest config

When the organizer announces the GZCTF base URL + game id:

1. Edit `configs/ai_contest.yaml`:
   - `gzctf.base_url`
   - `gzctf.game_id`
   - `gzctf.submit_payload_mode` (set per 5/9 finding)
   - `scope.allowed_domains` add the GZCTF host
2. Verify:

```bash
python scripts/ai_contest_supervisor.py --healthcheck-only
# expect [PASS] healthcheck
```

If healthcheck fails -> non-AI skill-contest fallback:

```bash
# Knowledge contest can remain human-operated with lookup tooling.
# Skill contest falls back to the non-AI path.
echo "fall back to non-AI skill-contest path"
```

## T+0 to T+10 (13:00 – 13:10) AI identity prep window

Per organizer rules: 10 minutes for human prep work.

| t | Action |
|---|---|
| +0:00 | Note real start time. Knowledge contest lookup tooling may run separately under human operation; the skill contest AI identity path starts here. |
| +1:00 | Open Claude Code in a wide terminal |
| +2:00 | `bash scripts/preflight.sh` again — quick sanity |
| +3:00 | If a test challenge is provided by the organizer, hit it via adapter once: `python scripts/ai_contest_supervisor.py --healthcheck-only` |
| +5:00 | Decide skill-contest path: if healthcheck PASS -> continue AI identity. If FAIL -> use the non-AI fallback. |
| +7:00 | Open a second terminal, `tail -f logs/ai-contest-*.jsonl` |
| +8:00 | Paste the solve-first prompt from `docs/loop_prompt_solve_first.md` into Claude Code |
| +9:00 | Verify supervisor's first heartbeat appears in tail |
| +10:00 | **STOP** typing.  No more human operations.  Watch only. |

## T+10 to T+170 (13:10 – 16:50) Unattended

Just watch:

- Heartbeats every ~60s in the tail
- `submit_outcome` events showing `kind=accepted`/`wrong`/etc.
- If `global_submit_disabled` appears, the supervisor will keep
  cataloging but stop submitting — you cannot intervene per rules.

If something goes catastrophically wrong (browser crash, network
down):

- Per AI-identity rules, **don't touch**.  The contest will award
  whatever the agent already accepted.
- Make notes for post-mortem.

## T+170 (16:50) – T+180 (17:00) Final 10 minutes

Watch the heartbeat.  If supervisor is still polling submits, leave
it alone.

## T+180 (17:00) Contest end

- Save final state:

```bash
cp state/ai_contest_state.json state/ai_contest_state-final-$(date +%Y%m%d-%H%M).json
cp logs/ai-contest-*.jsonl writeups/
```

- Post-game accepted count:

```bash
jq '.challenges | [.[]|select(.state=="accepted")] | length' \
   state/ai_contest_state.json
```

## 5/11 17:30 WriteUp deadline

The agent's JSONL log is the WriteUp source of truth.  Generate a
draft using the existing `ctf_agents/writeup/generate_writeup.py`
pipeline, then human-pad with introduction / context / per-challenge
narrative.

Per AI identity rules, the WriteUp must reflect that human did not
intervene during the run.  Be honest about which challenges the
agent solved vs which were blocked / no-agent / wrong-frozen.
