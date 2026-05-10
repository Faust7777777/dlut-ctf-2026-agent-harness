# GZCTF Adapter Needed Coverage

This document lists GZCTF API capabilities that should be designed for, even if not all are enabled in the contest run. The purpose is coverage: avoid discovering during the contest that a platform behavior was never modeled.

The design rule remains:

```text
supervisor = deterministic contest state machine
adapter = absorbs platform/API differences
guard = only submission gate
```

No endpoint in this document should bypass `FlagGuard` for flag submission.

## Existing P0 Chain

Already designed and locally tested:

```text
login/session
profile/current user
current team
game list/details
challenge list/detail
attachment download
flag submit
submit status polling
AnswerResult mapping
cookie auth
plaintext submit mode
basic encrypted-submit placeholder
```

The missing or partial areas below are not necessarily defects. They are the adapter coverage we should explicitly design so platform drift has a place to land.

## 1. Account / Session Coverage

Why it may be needed:

- Real DLUT may use SSO, cookie-only login, expired cookies, redirect-to-login, or disabled password auth.
- Profile may succeed while game endpoints fail due to team/game permission.
- Captcha or unified auth may make password login impossible.

Adapter methods to design:

```python
login() -> dict
verify_session() -> dict
profile() -> dict
logout() -> None
auth_diagnostics() -> AuthDiagnostics
```

`AuthDiagnostics` should classify:

```text
ok
not_logged_in
cookie_expired
sso_redirect
captcha_or_mfa_required
forbidden
unknown
```

Supervisor behavior:

- `not_logged_in/cookie_expired`: stop before contest loop and report.
- `sso_redirect/captcha_or_mfa_required`: stop and mark `AUTH_NEEDS_OPERATOR`.
- `forbidden`: stop and mark `AUTH_FORBIDDEN`.
- Never retry password login in a tight loop.

## 2. Team Coverage

Why it may be needed:

- Dynamic flags may be bound to team identity.
- Platform may require joining a team/game before seeing challenges or downloading attachments.
- Wrong team/session can cause `NotFound`, `Forbidden`, or `CheatDetected`.

Adapter methods to design:

```python
current_team() -> dict
team_members(team_id: int | None = None) -> list[dict]
team_games(team_id: int | None = None) -> list[dict]
team_diagnostics(game_id: int) -> TeamDiagnostics
```

`TeamDiagnostics` should include:

```text
team_id
team_name
joined_game
can_view_game
can_submit
```

Supervisor behavior:

- Run `team_diagnostics()` in `--healthcheck-only`.
- If `joined_game=false` or `can_submit=false`, no contest loop.

## 3. Game / Scoreboard / Time Window Coverage

Why it may be needed:

- Contest may not be open yet.
- Contest may end while supervisor is running.
- Scoreboard/team score is useful to detect whether Accepted actually credited.
- Game status can prevent submissions outside the allowed window.

Adapter methods to design:

```python
game(game_id: int) -> dict
game_details(game_id: int) -> dict
game_status(game_id: int) -> GameStatus
scoreboard(game_id: int) -> dict
team_score(game_id: int, team_id: int | None = None) -> dict
```

`GameStatus` should normalize:

```text
not_started
running
paused
ended
unknown
```

Supervisor behavior:

- If `not_started`: sync only; no submit.
- If `running`: normal.
- If `ended`: stop all submits; final summary only.
- If `paused/unknown`: conservative no-submit unless explicitly configured.

## 4. Challenge Metadata Coverage

Why it may be needed:

- Real challenge fields may differ from local GZCTF.
- Category names may use case/localization variants.
- Static/dynamic/container challenge types need different handling.
- Some descriptions contain critical instructions or per-instance hints.

Adapter methods to design:

```python
game_details(game_id: int) -> dict
normalize_challenges(payload: dict | list) -> list[ChallengeMeta]
challenge_detail(game_id: int, challenge_id: int) -> dict
normalize_challenge_detail(payload: dict) -> ChallengeDetail
```

Normalized metadata should include:

```text
id
title
category
type
score
solved
has_attachment
attachment_url
has_container
container_hint
description
raw
```

Supervisor behavior:

- Unknown fields go into `raw`, not into crash paths.
- Unknown challenge type is `analysis_only` unless attachment exists.
- Solved/accepted challenges are terminal locally.

## 5. Attachment Coverage

Why it may be needed:

- Attachments may require cookie auth.
- URL may be absolute, relative, signed, redirected, or one-time.
- Large attachments can blow time/token budget.
- Some attachments may be directories or multiple files.

Adapter methods to design:

```python
download_attachment(url: str, output_dir: Path) -> Path
download_challenge_attachments(game_id: int, challenge_id: int, output_dir: Path) -> list[Path]
attachment_diagnostics(url: str) -> AttachmentDiagnostics
```

`AttachmentDiagnostics` should classify:

```text
ok
not_found
forbidden
too_large
redirect_to_login
unsupported_mimetype
network_error
unknown
```

Supervisor behavior:

- `forbidden/redirect_to_login`: auth failure, no submit for that challenge.
- `too_large`: record and skip or request manual policy.
- `network_error`: retry with backoff.
- Hash downloaded files and avoid duplicate downloads.

## 6. Container Lifecycle Coverage

Why it may be needed:

- Web/Pwn challenges may require starting an instance.
- Dynamic flags may bind to team/container.
- Container IP/port can change on restart.
- A stale container can make a valid exploit produce the wrong flag.

Adapter methods to design:

```python
container_start(game_id: int, challenge_id: int) -> ContainerInfo
container_status(game_id: int, challenge_id: int) -> ContainerInfo
container_stop(game_id: int, challenge_id: int) -> None
container_extend(game_id: int, challenge_id: int) -> ContainerInfo
```

`ContainerInfo` should normalize:

```text
state: none | starting | running | stopped | expired | failed | unknown
host
ports
expires_at
instance_id
raw
```

Supervisor behavior:

- Container challenges are `analysis_only` unless explicitly enabled.
- Starting containers must be rate-limited.
- Candidate evidence must bind to current `instance_id` or current host/port.
- If container expires before submit, do not submit stale candidate.

## 7. Submit Payload Coverage

Why it may be needed:

- GZCTF frontend may encrypt flags before submission.
- Real deployment may use plaintext, encrypted, CSRF, or custom headers.
- Status can be immediate terminal or async pending.

Adapter methods to design:

```python
submit_flag_for_game(game_id: int, challenge_id: int, flag: str, ...) -> GZCTFSubmitOutcome
submit_payload_diagnostics(game_id: int, challenge_id: int) -> SubmitPayloadDiagnostics
```

Payload modes:

```text
plaintext
encrypted
auto
unsupported
```

Supervisor behavior:

- `auto` may try known safe modes, but must not double-submit the same flag.
- If mode is unknown, healthcheck should fail before contest run.
- Submit status must be polled through adapter only.

## 8. Submit Status / AnswerResult Coverage

Why it may be needed:

- Status may be returned as bare string, JSON envelope, nested data, or unknown enum.
- Unknown status must not be treated as Accepted.

Adapter methods to design:

```python
poll_submission_status(game_id: int, challenge_id: int, submit_id: int, ...) -> GZCTFSubmitOutcome
normalize_answer_result(payload: Any) -> GZCTFSubmitOutcome
```

Known normalized kinds:

```text
accepted
wrong
pending
cheat
not_found
rate_limited
forbidden
unknown
```

Supervisor behavior:

- `accepted`: mark challenge accepted.
- `wrong`: freeze challenge.
- `pending`: poll only; do not generate another candidate.
- `cheat`: global submit disabled.
- `unknown`: no resubmit; freeze as platform_unknown or leave pending with bounded retries.

## 9. Error Model Coverage

Why it may be needed:

- Real forks may return different error schemas.
- Some errors are retryable; some must stop all submits.
- HTTP 200 with error payload is common in some platforms.

Adapter methods to design:

```python
normalize_platform_error(status_code: int, payload: Any, text: str) -> PlatformError
```

`PlatformError` should include:

```text
kind
retryable
submit_safety
message
raw_excerpt
```

Kinds:

```text
auth_required
forbidden
not_found
rate_limited
csrf_or_payload_invalid
captcha_or_sso_required
server_error
network_error
schema_mismatch
unknown
```

Supervisor behavior:

- `auth_required/captcha_or_sso_required`: stop.
- `csrf_or_payload_invalid`: stop submit path.
- `rate_limited`: backoff.
- `schema_mismatch`: no submit until adapter patched.
- `unknown`: conservative no-submit for affected challenge.

## 10. Notice / Announcement Coverage

Why it may be needed:

- Organizers may announce flag format changes, challenge hotfixes, disabled challenges, or rule changes.
- AI identity may need to stop if organizers issue a platform warning.

Adapter methods to design:

```python
announcements(game_id: int | None = None) -> list[Announcement]
```

`Announcement` should include:

```text
id
title
content
created_at
severity
raw
```

Supervisor behavior:

- Read-only.
- Log new announcements.
- Do not auto-change submit policy unless announcement matches a strict configured rule.

## 11. Writeup Coverage

Why it may be needed:

- Not needed for live solving.
- Useful for post-contest summary or evidence export.

Adapter methods to design:

```python
writeup_status(game_id: int) -> dict
submit_writeup(game_id: int, content_path: Path) -> None
```

Contest behavior:

- Disabled during live run.
- Only available post-contest with explicit operator action.

## 12. Admin / Edit Coverage

Why it may be needed:

- Local GZCTF rehearsal needs challenge creation/import.
- Public bundle e2e needs local challenge creation.
- Official contest AI identity must not use admin/edit endpoints.

Separate local-only client:

```python
LocalGZCTFAdminAdapter
```

Methods:

```python
create_challenge(game_id: int, spec: LocalChallengeSpec) -> int
set_flag(challenge_id: int, flag: str) -> None
upload_attachment(challenge_id: int, path: Path) -> None
enable_challenge(challenge_id: int) -> None
delete_or_disable_challenge(challenge_id: int) -> None
```

Hard controls:

- Only enabled when `base_url` is localhost or explicitly `local_lab=true`.
- Never imported by contest supervisor.
- Never used with real DLUT base URL.
- Logs must say local lab only.

## 13. OpenAPI / Schema Coverage

Why it may be needed:

- If the real platform differs, a saved schema helps compare expected vs actual.
- Generated full SDK is heavy, but schema snapshot plus contract checks are useful.

Design:

```text
docs/gzctf_openapi_snapshot.json
scripts/check_gzctf_openapi_contract.py
```

Checks:

```text
endpoint exists
required fields accepted by adapter
known response shapes parse
AnswerResult enum includes expected values
challenge list shape parseable
submit/status path parseable
```

Do not replace hand-written adapter with a generated SDK during contest prep unless there is a clear reason. Use schema checks as guardrails, not as runtime complexity.

## 14. Adapter Capability Matrix

Add a runtime capability object:

```python
AdapterCapabilities(
    password_login: bool,
    cookie_login: bool,
    challenge_list: bool,
    attachments: bool,
    submit_plaintext: bool,
    submit_encrypted: bool,
    status_polling: bool,
    containers: bool,
    scoreboard: bool,
    announcements: bool,
    admin_local_lab: bool,
)
```

`--healthcheck-only` should print/log this matrix with secrets redacted.

## 15. Contest Safety Contract

No matter how much adapter coverage is added, these invariants must hold:

```text
No direct submit outside adapter.
No submit outside FlagGuard.
No automatic admin/edit usage in contest mode.
No unknown AnswerResult maps to accepted.
No duplicate candidate submit.
No new candidate while pending.
No submit after CheatDetected.
No full flag in logs/state/.secrets.
No stale dynamic-container candidate submit.
```

## Recommended Opus Task

Tell Opus:

```text
Read docs/gzctf_adapter_needed_coverage.md.
Do not implement everything at once.
First add interface/design stubs and contract tests for the coverage domains that are not yet modeled:
account diagnostics, team diagnostics, game status/scoreboard, container lifecycle, platform error normalization, announcement read-only, and local-only admin adapter.
Keep all new capabilities default disabled unless already needed by P0.
Do not change supervisor behavior except to consume normalized adapter outputs.
```

