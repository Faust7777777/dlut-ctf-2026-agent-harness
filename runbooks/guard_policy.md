# Guard Policy (AI Identity)

`ctf_agents/submit/flag_guard.py` is the only submit gate.  Under AI
identity there is **no human reviewer**, so the policy is configured so
that a valid candidate is never demoted to `HUMAN_REVIEW`.  Every
category is in the auto-submit set and confidence thresholds are 0.0.
Only hard gates remain: format, duplicate, rate limit, freeze, kill
switch, and CheatDetected.

| Setting | AI identity | Generic / legacy default |
|---|---|---|
| `auto_submit` | `true` | `false` (manual review default) |
| `auto_submit_categories` | `[misc, forensics, crypto, web, reverse, pwn]` | `[misc, forensics]` |
| `min_conf_auto_submit` | **`0.0`** | `0.92` |
| `min_conf_human_review` | **`0.0`** | `0.70` |
| `pwn_reverse_force_human_review` | **`false`** | `true` |
| `max_wrong_per_challenge` | **`1`** (1-strike freeze) | `2` |
| `min_seconds_between_submits_global` | `60` | `25` |
| `min_seconds_between_submits_per_challenge` | `300` | `90` |

`max_wrong_per_challenge: 1` is the AI-identity safety: there is no
human to reconsider, so one wrong submit on a challenge permanently
freezes it.

## Decision tree under AI identity (per FlagCandidate)

```
empty / format-bad        → REJECT          (hard gate)
challenge frozen          → HUMAN_REVIEW    (hard gate; effectively NO_OP)
kill-switch file present  → HUMAN_REVIEW    (operator panic button)
duplicate flag            → blocked above guard by supervisor dedupe
rate-window not open      → HOLD            (atomic claim; second concurrent
                                              decide degrades to HOLD)
otherwise                 → AUTO_SUBMIT
```

`HUMAN_REVIEW` is reached *only* by a hard-gate event (challenge already
frozen, kill switch active).  It is never reached by category or
confidence under the AI identity profile, because:

- every category is in `auto_submit_categories`
- `min_conf_auto_submit = 0.0` and `min_conf_human_review = 0.0`
- `pwn_reverse_force_human_review = false`

If you see HUMAN_REVIEW in the logs under AI identity, treat it as a
configuration error — the contest profile should never reach that
branch.

## What guard alone DOES NOT block

The supervisor's own duplicate gate sits *above* guard:

```text
sup.submitted_flag_hashes contains hash(candidate.flag) → skip
                                                       (no guard call)
```

This is intentional.  Guard's rate limit can be tuned; the duplicate
rule under AI identity must be hard-locked: the same flag must never
be submitted twice for the same challenge, regardless of timing.

## Generic FlagGuard machinery still supports HUMAN_REVIEW

`flag_guard.py` itself preserves the HUMAN_REVIEW capability — it is a
generic state machine and the unit tests (`tests/test_flag_guard.py`,
`scripts/rehearsal_5_9.py`) drive it with non-AI-identity config to
exercise every branch.  Under the AI identity yaml configs that path is
unreachable by design.

## Force-submit override

The `force_submit` channel exists in guard but the AI-identity
supervisor never invokes it.  Only the human can use:

```bash
python -m ctf_agents.submit.force_submit \
  --challenge-id <id> --flag '<flag>' --category <cat> \
  --reason '<≥10 chars>' --commit
```

Per AI-identity rules, the operator must NOT use this after t+10min.
Pre-rehearsal use is fine for unsticking a frozen-by-mistake challenge.

## Kill switch

`touch .auto_submit_off` in the project root immediately downgrades
all subsequent AUTO_SUBMIT decisions to HUMAN_REVIEW.  Removing the
file restores auto-submit.

Under AI identity, you create the kill switch only if you need to
stop the agent for a serious reason (suspected platform corruption,
mistaken target).  After t+10min, the operator should NOT touch it
unless the contest is already lost.

## Runtime capability preflight

`scripts/runtime_preflight.py` writes `state/runtime_capabilities.json`
listing per-category capability availability.  Under AI identity, the
supervisor does **not** silently demote categories with missing
capabilities — it surfaces the gap loudly through a
`category_capability_missing` event and refuses to start when a
required capability for a category in `auto_submit_categories` is
unavailable.  The operator must either install the missing capability
or remove the category from `auto_submit_categories` before retrying.

## State file

`logs/submission_state.json` (path from `submit.state_path` config)
is the only persistence of guard state.  Locking via `fcntl`, atomic
write, monotonic-clock-aware rate limit windows.  Do not edit by
hand during a run; the state machine cannot recover from external
mutation.
