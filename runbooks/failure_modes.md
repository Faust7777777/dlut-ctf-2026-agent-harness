# Failure Modes (AI Identity)

What to expect when something goes wrong + how the system contains it
**without** human intervention.  Read once; the supervisor is meant
to handle these autonomously per the AI-identity rule.

## Failure → response matrix

| Failure | Detection | Supervisor response | Operator action (≤ t+10) |
|---|---|---|---|
| Adapter login fails | `healthcheck_fail` event | Exit code 3 | Re-check `.env`, restart |
| Adapter HTTP 5xx on a tick | `step_error` event | Skip that challenge this tick, continue loop | None |
| Attachment 403/404 | `attachment_error` event | Continue without attachment; agent may still run | None |
| Submit HTTP error | `submit_error` event | Don't record outcome; next tick may re-attempt unless duplicate | None |
| `WrongAnswer` | `submit_outcome` event with `kind=wrong` | Freeze challenge; never resubmit | None |
| `CheatDetected` | `submit_outcome` + `global_submit_disabled` | Disable global submit, supervisor still observes new challenges but never submits again | Confirm post-game; investigate |
| `NotFound` | `submit_outcome` with `kind=not_found` | Freeze with `platform_not_found`; do not resubmit | Verify challenge ID config |
| `FlagSubmitted` past timeout | `submit_outcome` with `kind=pending` | Stay pending; next tick poll status; no resubmit (dedup) | None |
| State file corruption on restart | `state_corrupt_rotated` event | Old file moved to `.corrupt`, fresh state begins | None |
| Scope violation | `ScopeError` raised | Process crashes; supervisor exits non-zero | Re-check `scope.allowed_domains` |
| Disk full | OS error in `_save_state` | Exception propagates; process exits | Free space, restart |
| WSL clock backwards | Caught by `_compute_remaining` | Conservative wait full window | None |
| Process kill / OOM | Exit | On restart, state file resumes; accepted/frozen preserved | Restart supervisor |

## Anti-patterns to avoid (operator-side, ≤ t+10min only)

1. **Don't edit `state/ai_contest_state.json` by hand.**  The
   state machine ranks states monotonically; backwards edits don't
   roll back guard / freeze counters.

2. **Don't restart the lookup_service / supervisor mid-tick.**
   Wait for a heartbeat boundary (~ 60s) so any in-flight submit
   completes its poll cycle.

3. **Don't curl a flag manually.**  Even before t+10, manual curl
   bypasses guard's dedup; the supervisor will see the platform
   already submitted via session cookies and may double-submit.

4. **Don't run two supervisors concurrently.**  Guard locks on
   state file but adapter session + cookie jar do not — two
   concurrent supervisors is a race.

## After-contest debug entry points

- `logs/ai-contest-*.jsonl` — main supervisor event log
- `logs/submissions-*.jsonl` — every guard.record_outcome event
  (built into JsonlLogger by guard)
- `state/ai_contest_state.json` — final challenge state
- `state/snapshots/*.json` — periodic snapshots if you enabled them
- `artifacts/challenges/<id>/` — downloaded attachments

For a quick post-mortem:

```bash
# count terminal states
jq '.challenges | to_entries | group_by(.value.state) |
   map({state: .[0].value.state, n: length})' \
   state/ai_contest_state.json

# list accepted challenges
jq '.challenges | to_entries[] |
    select(.value.state=="accepted") | .key' \
   state/ai_contest_state.json
```
