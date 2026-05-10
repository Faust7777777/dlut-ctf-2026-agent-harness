# Solve-First Loop Policy

This document corrects the AI identity skill-contest operating model for Opus / Claude Code. Contest-day procedures live in `runbooks/contest_day_ai_identity.md`, which is the only current contest-day AI identity source of truth. The compact per-tick contract lives in `docs/opus_loop_contract.md`.

Boundary:

```text
Knowledge contest: may be handled by humans / separate lookup tooling.
Skill contest: main battlefield for AI identity, supervisor, guard, and solve-first loop.
```

The AI identity run is not a static benchmark where the solver may only use preinstalled one-shot tools. It is a 3-hour autonomous CTF solving session. The agent should behave like a careful contestant:

```text
inspect challenge -> reason -> run tools -> add minimal missing helper -> verify -> retry -> produce candidate or no_candidate
```

The supervisor still owns platform I/O and submission. The loop owns continued solving pressure.

Route control is persisted under each challenge in `state/ai_contest_state.json`; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate. The submit chain stays `supervisor -> validator -> FlagGuard -> adapter`.

Under AI identity the contest profile auto-submits every category (misc / forensics / crypto / web / reverse / pwn). There is no human reviewer to confirm anything, so confidence thresholds are 0.0, `pwn_reverse_force_human_review` is `false`, and a valid candidate is never demoted to HUMAN_REVIEW. Only hard gates (format / duplicate / rate-limit / freeze / kill-switch / CheatDetected) can stop a submission.

## Core Correction

Wrong behavior:

```text
tool/helper missing -> immediately NO_CANDIDATE
```

Correct behavior:

```text
tool/helper missing
-> identify exact missing capability
-> search local tools first
-> fetch or write minimal helper only for this challenge
-> run a toy/smoke verification
-> retry the original challenge
-> only then NO_CANDIDATE if still blocked
```

`NO_CANDIDATE` is allowed, but it must be earned. It cannot be the first response to a missing dependency, and it stays blocked until route exhaustion is proven by state.

## Roles

### Supervisor

`scripts/ai_contest_supervisor.py` remains deterministic:

```text
sync
download attachments
ingest candidate files
validate
FlagGuard
adapter submit/status
heartbeat
```

It does not install tools and does not conduct long reasoning.

### Opus / Claude Code

Opus is the always-on solving operator:

```text
read supervisor state/logs
pick unsolved challenge
inspect artifacts/challenges/<id>/
form hypothesis
run tools
call subagents
add minimal helpers when missing
write evidence and candidate files
let supervisor submit
```

### Subagents / Codex

Subagents may do bounded side tasks:

```text
reverse one binary
analyze one pcap
try one crypto attack
write one solver script
review one candidate
```

They must not submit flags or read secrets.

## Missing Capability Escalation Ladder

When blocked, Opus must classify the blocker.

```text
missing_tool
missing_helper
missing_runtime
missing_math_recipe
missing_target_service
unclear_problem
```

Then apply this ladder:

1. Check existing local tools.
2. Check repo tools and `tools/`.
3. Search installed package managers / Python modules.
4. Fetch a minimal public helper or write a small solver script.
5. Add a toy/smoke test proving the helper works, and record that as `capability_progress`, not `challenge_progress`.
6. Run the helper on the real challenge.
7. Record success or the exact remaining blocker.

Do not build a general framework unless it directly solves the current challenge.

## Allowed Environment Changes

Allowed during the 3-hour autonomous solving window:

```text
install a small missing package
vendor a small public helper with source URL and license note
write challenge-specific solver scripts under artifacts/challenges/<id>/
write reusable helper under tools/ctf_helpers/ only if it helps the current challenge
run local binaries, Sage, Python, z3, fpylll, pwntools, binwalk, tshark, Ghidra headless
```

Not allowed:

```text
call unauthorized targets
submit to public CTF platforms
read .env / .secrets / cookie jars
print webhook/API keys/passwords
modify supervisor/adapter/guard during solving unless explicitly fixing a blocking bug
disable FlagGuard
directly submit with curl/requests/browser
```

## Evidence Files Per Challenge

Each real solving attempt should leave:

```text
artifacts/challenges/<id>/cc_hypothesis.md
artifacts/challenges/<id>/tool_gap.md             # only when a gap exists
artifacts/challenges/<id>/subagent_request.md
artifacts/challenges/<id>/subagent_reply.md
artifacts/challenges/<id>/cc_final_decision.md
artifacts/challenges/<id>/codex_candidates.json
```

If a helper is added:

```text
artifacts/challenges/<id>/helper_source.md
artifacts/challenges/<id>/helper_smoke_test.txt
artifacts/challenges/<id>/solver_run.txt
```

## Candidate Rules

Produce `codex_candidates.json` only when:

```text
candidate came from current challenge artifacts
evidence paths exist and stay inside artifacts/challenges/<id>/
confidence is justified
candidate is not a sample flag
candidate is not from logs/state/writeups/expected metadata
dynamic/container binding is current if applicable
```

If blocked after the escalation ladder, write:

```json
[]
```

and explain the blocker in `cc_final_decision.md`.

If the helper smoke pass succeeds but the real challenge still yields no candidate, keep the result in the capability bucket; that alone does not advance the challenge.

## Loop Behavior

The `/loop` watchdog should not just keep the process alive. Each tick should ask:

1. Is supervisor alive and heartbeating?
2. Are there unsolved downloaded challenges?
3. Which challenge has the best expected progress per minute?
4. Is it blocked by a missing tool/helper?
5. If yes, apply the missing capability escalation ladder.
6. If no, continue direct solving.
7. If a candidate exists, leave it for supervisor ingestion.
8. End the turn so the next `/loop` tick can run.

## No-Candidate Standard

`NO_CANDIDATE` is valid only when the notes show:

```text
what was tried
what tool/helper was missing
what was installed or fetched
what toy/smoke test passed or failed
why the original challenge still did not yield a candidate
why guessing would be unsafe
```

This prevents the model from prematurely stopping at "tool not found".

## Example: Multivariate Coppersmith

Bad:

```text
Sage/helper not available -> cannot solve -> []
```

Good:

```text
Need multivariate Coppersmith
-> check Sage/fpylll
-> search for a small public Sage helper
-> vendor into tools/ctf_helpers/crypto/ with source note
-> run toy small-root regression
-> run against challenge
-> if lattice still fails, write exact failure and []
```

The helper is not a new platform. It is a challenge-specific capability needed to solve the current crypto task.

## Success Criteria

This policy is working if:

```text
Opus keeps solving during loop ticks
missing tools trigger minimal capability fill, not immediate no_candidate
helpers have smoke tests
candidate files still pass validator
all submits still pass FlagGuard and GZCTFAdapter
logs/state/.secrets still do not contain full flags or secrets
```
