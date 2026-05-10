# Opus Handoff - AI Identity Skill Contest Plan

Date: 2026-05-08

This is a context handoff for Opus. The current contest-day AI identity source of truth is `runbooks/contest_day_ai_identity.md`; the current loop model is the solve-first series in `docs/solve_first_loop_policy.md` and `docs/loop_prompt_solve_first.md`.

## Executive Decision

We are preparing the **skill contest** for **AI identity** first, but the final choice is gated on a real GZCTF rehearsal on 2026-05-09.

Boundary:

```text
Knowledge contest: may be handled by humans / separate lookup tooling.
Skill contest: main battlefield for AI identity, supervisor, guard, and solve-first loop.
```

Decision rule:

```text
If 5/9 P0 rehearsal passes -> choose AI identity.
If any skill-contest P0 item fails -> use the non-AI fallback.
```

The user accepts that AI identity may score lower or even fail in the skill contest. The goal is to demonstrate a real autonomous Agent loop, not to maximize human-assisted skill-contest score.

## Rules Interpretation

AI identity constraints from organizer screenshot:

- Human gets 10 minutes after start to prepare: e.g. configure, download, deploy AI.
- After 10 minutes, no human operations.
- The whole contest permits only one prompt to the Agent.
- Agent tools are allowed.

Practical implication:

- Build for a **single-prompt autonomous loop**.
- After the prompt, no human confirmation, no manual submit, no manual fix, no second prompt.
- Claude Code can be the orchestration shell, but not the only state machine.

## Recommended Architecture

Use:

```text
Claude Code + local deterministic supervisor
```

Boundaries:

| Component | Responsibility | Must Not Do |
|---|---|---|
| Claude Code | Read runbooks, start supervisor, watch logs, optionally call Codex for sidecar exploration/review/patches | Directly submit flags, hand-roll HTTP submit, treat chat context as state |
| `scripts/ai_contest_supervisor.py` | Main deterministic state machine: loop, sync game, download, call agents, call guard, submit, poll status, heartbeat, recovery | Open-ended LLM reasoning, bypass guard |
| GZCTF adapter | Only platform I/O: login, profile, team, game, challenge detail, attachment, container, submit, status | Decide whether a flag is safe |
| `FlagGuard` | Only submit gate: format, confidence, category, freeze, rate limit, duplicate policy | Access platform or solve challenges |
| Agents | Produce candidate + evidence | Submit or mutate global state |
| Codex subagent | Sidecar exploration/review/patch/log analysis | Read secrets, change `state/ai_contest_state.json`, submit, unfreeze, modify guard |
| State/logs | Only source of runtime truth | Store plaintext secrets |

Core invariants:

```text
guard is the only submit gate
adapter is the only platform I/O
state/log files are the only source of truth
LLM guesses are never flags
Codex outputs are never directly submitted
```

## GZCTF API Facts

Official API docs are available and should be treated as the adapter baseline:

- Scalar docs: `https://gzctf.gzti.me/scalar.html`
- OpenAPI JSON: `https://gzctf.gzti.me/openapi.json`

Confirmed endpoints:

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

Submit/status shape:

- Submit body in OpenAPI: `{ "flag": "..." }`
- Submit returns integer `submitId`.
- Status endpoint returns `AnswerResult`.
- `AnswerResult` enum:
  - `FlagSubmitted`
  - `Accepted`
  - `WrongAnswer`
  - `CheatDetected`
  - `NotFound`

Important nuance from Pro/Codex review:

- The API paths are not unknown.
- But the real deployed submit payload still needs validation.
- GZCTF frontend source shows `encryptApiData(..., config.apiPublicKey)` on flag submit in some versions. Therefore the adapter must support, or at least explicitly test, whether the real deployment expects plaintext or encrypted `flag`.

Adapter design requirement:

```yaml
submit_payload_mode: plaintext | encrypted | auto
api_public_key: optional
```

5/9 should determine the real mode.

## Current Codebase Facts

Existing relevant files:

```text
ctf_agents/submit/platform_adapter.py
ctf_agents/submit/ctfd_adapter.py
ctf_agents/submit/flag_guard.py
ctf_agents/submit/state_store.py
ctf_agents/skill/workflow.py
ctf_agents/skill/agents/misc_real.py
scripts/skill_workflow_dryrun.py
scripts/skill_workflow_realctf.py
scripts/rehearsal_5_9.py
```

Existing good pieces:

- `FlagGuard` exists.
- `SubmissionStateStore` has file locks, atomic JSON writes, rate limit slot claiming, wrong count, freeze.
- `SkillWorkflow` can route challenge -> agent -> guard -> adapter.
- `DryRunAdapter` and `CTFdAdapter` show the existing adapter style.
- Real Misc agent exists and has run against true BJDCTF fixtures.

Existing gaps for AI identity:

- No GZCTF adapter yet.
- No AI-identity supervisor.
- No single-prompt runbook.
- Current `FlagGuard` does **not** appear to block duplicate same-flag submissions by hash.
- Current default freeze threshold may be 2 wrongs; AI identity policy should be `max_wrong_per_challenge: 1`.
- Existing workflow is challenge-by-challenge; AI identity needs a long-running loop with game sync, heartbeat, pending submit polling, crash recovery.

## P0 Work For Opus

### P0-1: GZCTF Adapter

Create:

```text
ctf_agents/submit/gzctf_adapter.py
tests/test_gzctf_adapter.py
```

Do not overbuild a full SDK. Build the minimum contest path.

Required API:

```python
class GZCTFAdapter:
    def __init__(self, base_url: str, *, username: str | None = None,
                 password: str | None = None, cookie_jar_path: str | None = None,
                 token: str | None = None, scope_cfg: dict | None = None,
                 submit_payload_mode: str = "auto",
                 api_public_key: str | None = None):
        ...

    def login(self) -> dict: ...
    def profile(self) -> dict: ...
    def current_team(self) -> dict: ...
    def list_games(self, count: int = 50, skip: int = 0) -> dict: ...
    def game(self, game_id: int) -> dict: ...
    def game_details(self, game_id: int) -> dict: ...
    def challenge_detail(self, game_id: int, challenge_id: int) -> dict: ...
    def download_attachment(self, url: str, output_dir: str | Path) -> Path: ...
    def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult: ...
    def submit_flag_for_game(self, game_id: int, challenge_id: int, flag: str) -> SubmitResult: ...
    def poll_submission_status(self, game_id: int, challenge_id: int, submit_id: int,
                               timeout_s: float = 60, interval_s: float = 2) -> SubmitResult: ...
    def create_container(self, game_id: int, challenge_id: int) -> dict: ...
    def delete_container(self, game_id: int, challenge_id: int) -> dict: ...
    def extend_container(self, game_id: int, challenge_id: int) -> dict: ...
```

`submit_flag()` should satisfy existing `PlatformAdapter` protocol, probably using a configured `game_id`.

Status mapping:

| GZCTF status | `SubmitResult.correct` | Notes |
|---|---:|---|
| `Accepted` | `True` | final success |
| `WrongAnswer` | `False` | final wrong |
| `CheatDetected` | `False` | also trigger global submit disable in supervisor |
| `NotFound` | `None` or special raw status | treat as platform/config anomaly, not ordinary wrong |
| `FlagSubmitted` | `None` | pending, keep polling |

Tests must cover:

- login request and cookie/session reuse
- profile/team/game/details parsing
- challenge detail with `context.url`
- attachment download uses same session
- submit returns submitId
- poll `FlagSubmitted -> Accepted`
- poll `FlagSubmitted -> WrongAnswer`
- `CheatDetected` maps to non-success and is visible in raw
- `NotFound` is not treated as accepted
- timeout while still `FlagSubmitted` does not re-submit
- plaintext/encrypted mode branch can be selected, even if encryption is initially a stub marked `NEEDS_REAL_INSTANCE_VALIDATION`

Security:

- Never log password/cookie/token.
- Redact flags in ordinary logs.
- Use `assert_url_in_scope()` for base URL, API URL, and attachment URL.

### P0-2: AI Contest Supervisor

Create:

```text
scripts/ai_contest_supervisor.py
tests/test_ai_contest_supervisor.py
```

Minimum behavior:

1. Read `configs/ai_contest.yaml` and `.env`.
2. Instantiate `GZCTFAdapter`.
3. Run healthcheck: login/profile/team/game/challenge visibility.
4. Sync challenge list from `/api/game/{id}/details`.
5. Build local challenge state.
6. Fetch challenge detail.
7. Download attachment if present.
8. Dispatch only safe agents initially: `misc_real` for `Misc`/`Forensics`/static attachment style tasks.
9. Normalize candidate into `FlagCandidate`.
10. Call `FlagGuard.decide()`.
11. Submit only when guard returns `AUTO_SUBMIT`.
12. Poll status until terminal or timeout.
13. Record outcome via `FlagGuard.record_outcome()`.
14. Mark Accepted challenges complete and stop further submit.
15. WrongAnswer -> frozen after one wrong under AI identity config.
16. Write heartbeat every 60 seconds.
17. Write JSONL events for every major step.
18. On restart, load state and do not re-submit accepted/frozen/duplicate flags.

Required state files:

```text
state/ai_contest_state.json
state/snapshots/*.json
logs/ai-contest-<ts>.jsonl
logs/submissions-<ts>.jsonl
artifacts/challenges/<challengeId>/
locks/global_submit.lock
locks/challenge_<id>.lock
```

Challenge state minimum shape:

```json
{
  "challenge_id": "123",
  "title": "Example",
  "category": "Misc",
  "type": "StaticAttachment",
  "state": "discovered",
  "attachment_paths": [],
  "candidates": [],
  "submitted_flag_hashes": [],
  "accepted_flag_hash": null,
  "wrong_count": 0,
  "freeze_reason": null,
  "last_submit_id": null,
  "last_update": "2026-05-08T00:00:00+08:00"
}
```

AI identity policy:

```yaml
submit:
  auto_submit: true
  max_wrong_per_challenge: 1
  min_seconds_between_submits_global: 60
  min_seconds_between_submits_per_challenge: 300
  auto_submit_categories: ["misc", "forensics", "crypto"]
  pwn_reverse_force_human_review: true
```

Duplicate rule:

- Compute normalized flag hash.
- If `(challenge_id, flag_hash)` already exists in local state or submission log, do not submit.
- This can be implemented in supervisor first if modifying `FlagGuard` is too risky.

Tests must cover:

- first sync creates state
- attachment challenge gets downloaded
- no candidate -> no submit
- candidate -> guard allow -> submit -> poll accepted -> accepted state
- candidate -> wrong -> wrong_frozen and no second submit
- same flag candidate twice -> only one submit
- pending `FlagSubmitted` -> does not re-submit
- `CheatDetected` -> global submit disabled
- restart from state -> no repeated accepted/frozen submissions

### P0-3: Mock GZCTF Server / Rehearsal

Create or extend:

```text
tests/fixtures/mock_gzctf_server.py
scripts/rehearsal_ai_identity.py
```

The mock should provide:

```text
POST /api/account/login
GET  /api/account/profile
GET  /api/team
GET  /api/game/{id}/details
GET  /api/game/{id}/challenges/{challengeId}
POST /api/game/{id}/challenges/{challengeId}
GET  /api/game/{id}/challenges/{challengeId}/status/{submitId}
```

Scenarios:

- Accepted path.
- WrongAnswer path.
- `FlagSubmitted` pending then Accepted.
- NotFound.
- CheatDetected.
- Duplicate candidate.
- Attachment download.

`scripts/rehearsal_ai_identity.py` should run:

```text
mock server -> supervisor -> guard -> adapter -> status poll -> summary JSON
```

Output:

```text
logs/rehearsal-ai-identity-<ts>.jsonl
logs/rehearsal-ai-identity-<ts>-summary.json
```

### P0-4: Runbooks

Create:

```text
runbooks/ai_identity.md
runbooks/gzctf_adapter.md
runbooks/guard_policy.md
runbooks/failure_modes.md
runbooks/contest_day_ai_identity.md
```

`runbooks/contest_day_ai_identity.md` is the only current contest-day AI identity source of truth. `docs/loop_prompt_solve_first.md` owns the solve-first single prompt and recurring `/loop` instruction.

## 5/9 Go / No-Go Gate

P0 pass criteria on real GZCTF test environment:

```text
1. Real base URL reachable.
2. login/profile/team works.
3. game/challenge list works.
4. challenge detail works.
5. at least one attachment downloads via adapter/session.
6. submit returns submitId.
7. status polls to terminal state or known valid pending behavior.
8. submit payload mode (plaintext/encrypted) is known.
9. supervisor starts from one prompt/command and enters loop.
10. guard is the only submit path.
11. duplicate flag is blocked locally.
12. WrongAnswer freezes after one wrong.
13. Accepted stops that challenge.
14. rate limit works.
15. state, JSONL, submissions log, heartbeat work.
16. unattended run lasts at least 30 minutes.
```

No-Go if any of these fail:

```text
login/profile/team/game/challenge fails
attachment download fails
submit/status cannot be validated
submit payload mode remains unknown
supervisor cannot single-prompt start
guard can be bypassed
WrongAnswer can repeat-submit
30-minute unattended run fails
state/logs are unreliable
```

Not fatal:

```text
container API fails -> static attachment only
Codex plugin fails -> disable Codex
Web/Pwn/Reverse agents immature -> analyze only, no auto-submit
```

## 10-Minute Contest Prep Plan

Target actions:

```text
0:00-1:00  prepare workspace, run prepare_10min script
1:00-2:30  write base URL, account/cookie, gameId/teamId/divisionId into env/config
2:30-4:00  healthcheck login/profile/team/game/challenge
4:00-5:30  download one attachment through adapter
5:30-6:30  submit/status healthcheck if organizer test challenge allows
6:30-7:30  if skill-contest P0 failed, use the non-AI fallback; if P0 passed, keep AI identity
7:30-8:30  start Claude Code and enter the single prompt
8:30-10:00 observe heartbeat only; after 10:00 no human action
```

## What Not To Do

Do not spend P0 time on:

- Full Web/Pwn/Reverse automation.
- Fancy dashboards.
- Full SQLite migration.
- Large refactors of existing guard/workflow.
- Codex plugin integration before adapter/supervisor works.
- LLM suggester.
- Aggressive multi-agent concurrency.

Do not in AI identity runtime:

- Submit without guard.
- Let Claude/Codex submit directly.
- Store plaintext secrets.
- Store complete unredacted flags in ordinary logs.
- Continue auto-submitting after WrongAnswer.
- Treat `FlagSubmitted` timeout as permission to resubmit.
- Treat `NotFound` as Accepted.
- Treat Codex output as proof.

## Immediate Message To User After P0 Implementation

When Opus finishes P0, report in this exact shape:

```text
1. GZCTF adapter:
   login/profile/team/game/details/challenge/attachment/submit/status = PASS/FAIL
   submit_payload_mode = plaintext/encrypted/unknown

2. supervisor:
   single-command start = PASS/FAIL
   state/log/heartbeat = PASS/FAIL
   accepted stop = PASS/FAIL
   wrong freeze = PASS/FAIL
   duplicate block = PASS/FAIL
   rate limit = PASS/FAIL

3. rehearsal:
   mock AI identity = PASS/FAIL
   30-min unattended real/test env = PASS/FAIL or NOT_RUN

4. Go/No-Go:
   AI identity recommended = YES/NO
   blockers = [...]
```
