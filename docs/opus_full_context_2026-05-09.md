# Opus Full Context Handoff - AI Identity Skill Contest

Date: 2026-05-09
Owner: Opus as main implementer, Codex as reviewer / sidecar design reviewer

This is a context handoff for Opus if it has lost history. The current contest-day AI identity source of truth is `runbooks/contest_day_ai_identity.md`; the current loop model is the solve-first series in `docs/solve_first_loop_policy.md` and `docs/loop_prompt_solve_first.md`.

## 1. Executive Summary

We are preparing the DLUT **skill contest** as an **AI identity** run, but the final choice is still gated by a real GZCTF rehearsal on 5/9.

Boundary:

```text
Knowledge contest: may be handled by humans / separate lookup tooling.
Skill contest: main battlefield for AI identity, supervisor, guard, and solve-first loop.
```

Decision rule:

```text
If 5/9 real GZCTF P0 rehearsal passes -> choose AI identity.
If any skill-contest P0 item fails -> use the non-AI fallback.
```

This is not the maximum-score skill-contest plan. The user explicitly wants to "赌自动 Agent 展示效果": a credible autonomous agent loop, even if expected score is lower than a human-operated skill-contest fallback.

Core architecture:

```text
Claude Code + /loop = orchestration shell and watchdog
scripts/ai_contest_supervisor.py = deterministic contest state machine
GZCTFAdapter = only platform I/O
FlagGuard = only submit gate
Codex plugin = optional sidecar exploration / review, never submit
state/log files = runtime source of truth
```

## 2. User Intent And Constraints

The user has repeatedly clarified:

- Knowledge contest may be done by humans and separate lookup tooling.
- Skill contest is the AI identity main track.
- The user leans toward AI identity if 5/9 rehearsal passes.
- The user accepts that AI identity may score only 1-2 static Misc/Forensics tasks or even fail; the point is autonomous agent demonstration.
- The user wants Claude Code as the main visible agent because:
  - Claude Code has large context.
  - Claude Code supports `/loop` scheduled tasks.
  - Claude Code can call Codex via `codex-plugin-cc`.
- The user wants Codex to act as a subagent / sidecar:
  - exploring challenge directories,
  - reviewing candidates,
  - patching small local helper scripts,
  - diagnosing logs.
- The user does not want Codex/Opus to keep warning that the provided test account is sensitive. Treat it as an agent test account, but still do not write secrets into git, shell history, process arguments, or logs.

Operational constraints from organizer rules as currently understood:

```text
10 minutes after start: human may prepare / deploy AI.
After t+10min: no human operations.
Whole contest: only one prompt to the Agent.
Agent tools are allowed.
```

Conservative interpretation:

```text
Human can configure URL/auth and start the single prompt during the 10-minute window.
After that, Claude/supervisor/Codex must run without human prompts or manual fixes.
No direct manual flag submit after t+10min.
```

## 3. Pro's Strategic Advice

Pro's core conclusion:

```text
5/9 达标后再选 AI 身份。不是现在无条件选，也不是放弃。
```

If the skill-contest goal is stable score:

```text
Use the non-AI fallback.
```

If goal is an autonomous Agent demonstration:

```text
AI identity is worth betting on only if the real 5/9 GZCTF single-prompt rehearsal passes.
```

Pro strongly recommended:

```text
Claude Code + local supervisor
```

Not:

```text
Pure Claude Code as the only 4-hour state machine.
```

Reason:

- Claude context is not a database.
- `/loop` is not a precise scheduler.
- submit/status/freeze/rate-limit must be deterministic and persisted.
- If Claude gets stuck in a long turn, the Python supervisor should still keep polling and preserving state.

No-Go if any of these fail on 5/9:

```text
login/profile/team/game/challenge
attachment download
submit/status
guard as only submit path
single-prompt supervisor start
30-minute unattended run
state/log/heartbeat
WrongAnswer freeze
duplicate submit block
pending submit not resubmitted
```

Lower-priority / not blockers:

```text
Codex plugin fails -> disable Codex.
container API fails -> focus static attachment tasks.
Web/Pwn/Reverse agents immature -> skip or notes only.
```

## 4. Current Implementation Status

The following are implemented and reviewed:

```text
ctf_agents/submit/gzctf_adapter.py
scripts/ai_contest_supervisor.py
tests/test_gzctf_adapter.py
tests/test_ai_contest_supervisor.py
tests/fixtures/mock_gzctf_server.py
scripts/rehearsal_ai_identity.py
configs/ai_contest.example.yaml
runbooks/ai_identity.md
runbooks/gzctf_adapter.md
runbooks/guard_policy.md
runbooks/failure_modes.md
runbooks/contest_day_ai_identity.md
runbooks/campus_sso_cookie_reuse.md
```

The current reviewed invariants:

```text
Official OpenAPI status bare string AnswerResult is supported.
Official GameDetailModel.challenges dict-by-category is supported.
Legacy list challenge shape remains compatible.
Pending state never calls agent / guard / submit for a second flag.
Pending -> wrong syncs supervisor state and FlagGuard wrong/frozen state.
Pending -> accepted records final accepted outcome in guard history.
auth_mode=password/cookie/auto works.
cookie mode never POSTs password.
auto mode tries loaded cookies first, then password fallback.
Cookie loader supports Netscape cookies.txt, JSON array, and legacy dict.
Same cookie name across different domain/path is not overwritten.
Mock GZCTF now matches official OpenAPI shape: bare AnswerResult + dict-by-category challenges.
```

Recent verification by Codex:

```text
tests/test_gzctf_adapter.py: 43 tests OK
tests/test_ai_contest_supervisor.py: 15 tests OK
bash scripts/run_all_tests.sh: ALL CHECKS PASSED
```

Real campus SSO behavior verified against `software.dlut.edu.cn`:

```text
Windows Chrome SSO login -> export cookies -> WSL curl cookie jar -> HTTP 200
Netscape jar loads.
JSON array loads.
Same-name cross-domain cookies remain distinct.
requests sends only matching target-domain cookies.
```

This verifies the cookie-reuse mechanism we expect to need if real GZCTF is behind DLUT SSO.

## 5. GZCTF API Facts

Official docs:

```text
Scalar UI:
https://gzctf.gzti.me/scalar.html

OpenAPI JSON:
https://gzctf.gzti.me/openapi.json
```

Important endpoints:

```text
POST   /api/account/login
GET    /api/account/profile
GET    /api/team
GET    /api/game
GET    /api/game/recent
GET    /api/game/{id}
POST   /api/game/{id}
GET    /api/game/{id}/details
GET    /api/game/{id}/challenges/{challengeId}
POST   /api/game/{id}/challenges/{challengeId}
GET    /api/game/{id}/challenges/{challengeId}/status/{submitId}
POST   /api/game/{id}/container/{challengeId}
DELETE /api/game/{id}/container/{challengeId}
POST   /api/game/{id}/container/{challengeId}/extend
```

Important schema details:

```text
GameDetailModel.challenges = Dictionary<string, ChallengeInfo[]>
AnswerResult = bare JSON string enum:
  FlagSubmitted
  Accepted
  WrongAnswer
  CheatDetected
  NotFound
FlagSubmitModel = {"flag": "..."} in OpenAPI
POST submit returns int submitId in OpenAPI
```

Still to verify on real 5/9 deployment:

```text
submit_payload_mode = plaintext or encrypted
```

Reason: GZCTF frontend versions may encrypt flag submit via `encryptApiData(..., config.apiPublicKey)`. OpenAPI says plaintext, but real deployment must be tested.

## 6. Claude Code `/loop` Interpretation

The user wants to use Claude Code `/loop` to keep the agent alive.

Important design decision:

```text
/loop is not the contest engine.
/loop is a watchdog and correction layer.
The local Python supervisor is the contest engine.
```

Why:

- `/loop` only fires when Claude Code is running and idle.
- If Claude is in a long turn, `/loop` waits until that turn ends.
- `/loop` is not a deterministic state machine.
- If terminal/session issues occur, persisted local state must be the source of truth.

Correct structure:

```text
Claude Code stays open.
Claude's initial single prompt starts or verifies supervisor.
Supervisor runs as a background/local process and keeps syncing/polling.
/loop every 3 minutes checks local heartbeat/state/logs.
If supervisor is dead or stale, /loop follows failure_modes runbook.
Codex can be called only as a sidecar.
```

Do not design:

```text
/loop every few minutes decides the next flag to submit.
Claude Code itself directly calls submit.
Codex directly calls GZCTF.
```

Recommended `/loop` frequency:

```text
/loop 3m
```

Reason:

- 1 minute is noisy and can waste context/calls.
- 5 minutes is slower for watchdog response.
- 3 minutes is a reasonable watchdog interval.

## 7. Current Contest-Day Prompt

Do not preserve or paste older prompt variants from this handoff. For contest day, use only:

```text
runbooks/contest_day_ai_identity.md
docs/loop_prompt_solve_first.md
docs/solve_first_loop_policy.md
configs/ai_contest.yaml
```

`runbooks/contest_day_ai_identity.md` is the only current contest-day AI identity source of truth. `docs/loop_prompt_solve_first.md` contains the solve-first single prompt and recurring `/loop` instruction.

## 8. Codex Plugin / Sidecar Role

This is the missing piece the user asked about: `codex-plugin-cc`.

Repository:

```text
https://github.com/openai/codex-plugin-cc
```

User's desired flow:

```text
Claude Code is main program.
Claude Code uses codex-plugin-cc to call Codex.
Codex is a subagent for exploration / review.
```

Boundary:

```text
Codex is P2 sidecar, not P0 contest engine.
Codex failure must not break the supervisor.
Codex must never submit.
Codex must never read .env or .secrets.
Codex must never mutate state/ai_contest_state.json.
Codex must never unfreeze challenges.
Codex must never call GZCTF API.
```

Allowed Codex tasks:

```text
Inspect one challenge artifact directory.
Summarize file structure.
Run local non-platform tools on copied attachments.
Propose candidate with evidence paths.
Review a candidate generated by misc agent.
Patch small local helper scripts if Claude asks and if not touching secrets/state.
Diagnose local logs after supervisor error.
```

Fixed output paths:

```text
artifacts/challenges/<challenge_id>/codex_notes.md
artifacts/challenges/<challenge_id>/codex_candidates.json
artifacts/challenges/<challenge_id>/patches/*.patch
```

Candidate schema:

```json
{
  "challenge_id": "123",
  "candidate": "flag{...}",
  "confidence": "low|medium|high",
  "evidence_paths": ["artifacts/challenges/123/evidence/..."],
  "submit_recommendation": "never_direct_submit",
  "notes": "short rationale"
}
```

Even a high-confidence Codex candidate is still:

```text
Codex output -> Claude/supervisor parser -> FlagCandidate -> FlagGuard -> adapter submit/status
```

Never:

```text
Codex output -> submit
```

## 9. What Opus Should Do Next

Do not expand Web/Pwn/Reverse agents now. Do not refactor the supervisor unless real 5/9 data forces it.

Next useful task:

```text
P2: Codex sidecar/plugin dry-run
```

Recommended implementation scope:

1. Create `runbooks/codex_sidecar.md`.
2. Document plugin install/verification commands for `codex-plugin-cc`.
3. Define allowed/forbidden Codex behavior exactly as above.
4. Define artifact paths and candidate schema.
5. Add a mock sidecar dry-run that does not require real Claude Code plugin if direct plugin automation is not scriptable:
   - create a fake challenge artifact directory,
   - write sample `codex_notes.md`,
   - write sample `codex_candidates.json`,
   - validate schema,
   - ensure supervisor/guard path would still be required for submission.
6. Add `codex_sidecar.enabled` config stanza if needed, default off or safe:

```yaml
codex_sidecar:
  enabled: false
  max_parallel_tasks: 1
  timeout_s: 600
  allow_patch: true
  allow_submit: false
  allow_secret_read: false
```

Do not put Codex sidecar on the critical path for 5/9. If it is not ready:

```text
Disable Codex and run supervisor alone.
```

## 10. 5/9 Real Environment Checklist

When organizer gives the real URL:

```text
1. curl -I <real base URL>
2. Browser login GZCTF / campus SSO.
3. Export Netscape jar or Cookie-Editor JSON array into .secrets/.
4. Edit configs/ai_contest.yaml:
   - gzctf.base_url
   - gzctf.game_id
   - gzctf.auth_mode = cookie or auto
   - gzctf.cookie_jar_path
   - scope.allowed_domains
   - submit_payload_mode = auto initially
5. source tools/env.sh
6. python scripts/ai_contest_supervisor.py --config configs/ai_contest.yaml --healthcheck-only
7. Verify profile/team/game/details.
8. Fetch one challenge detail.
9. Download one attachment via adapter.
10. Submit one organizer-provided test flag or controlled wrong flag.
11. Poll status to terminal.
12. Pin submit_payload_mode = plaintext or encrypted.
13. Run 30 minutes unattended on test env.
```

Go if all P0 pass:

```text
AI identity recommended.
```

No-Go if any skill-contest P0 fails:

```text
non-AI skill-contest fallback.
```

## 11. Known Real-World Network Facts

The user connected to campus network. Codex checked:

```text
WSL has network via NAT.
software.dlut.edu.cn is reachable.
software.dlut.edu.cn redirects to DLUT SSO.
Windows Chrome SSO login can be exported and reused from WSL.
```

Do not assume real GZCTF is reachable until the real URL is tested with `curl -I` and `--healthcheck-only`.

## 12. Things Not To Do Now

Do not:

```text
Build advanced Web/Pwn/Reverse automation.
Add aggressive scanners.
Increase submit rate.
Let Codex call submit/status.
Let Claude Code hand-roll GZCTF HTTP calls.
Make /loop the main solver.
Store cookies outside .secrets.
Log cookie/token/password/full flag.
Refactor the whole state model before 5/9.
```

## 13. Current Bottom Line

Current status:

```text
Mock/schema/cookie/supervisor/guard pipeline is reviewed and green.
Real GZCTF URL is still missing.
Real submit/status and payload mode are still unverified.
Codex plugin sidecar is still not integrated.
```

Next:

```text
Opus should either:
  A. stay frozen until real 5/9 URL arrives, or
  B. implement only the P2 Codex sidecar dry-run/runbook with strict boundaries.
```

The user's likely preference right now is B, because the user explicitly asked that the automation plugin chain is still missing.
