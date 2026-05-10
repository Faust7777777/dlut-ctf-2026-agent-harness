# GZCTF Adapter Runbook

`ctf_agents/submit/gzctf_adapter.py` is the only egress to the GZCTF
platform.  Read this when you need to debug a platform call, change
the submit payload mode, or extend the API surface.

## Endpoints (the contest path only)

```text
POST   /api/account/login                       login()
GET    /api/account/profile                     profile()
GET    /api/team                                current_team()
GET    /api/game                                list_games()
GET    /api/game/{id}                           game()
GET    /api/game/{id}/details                   game_details()
GET    /api/game/{id}/challenges/{cid}          challenge_detail()
POST   /api/game/{id}/challenges/{cid}          submit_flag_for_game()
GET    /api/game/{id}/challenges/{cid}/status/{sid}   poll_submission_status()
POST   /api/game/{id}/container/{cid}           create_container()
DELETE /api/game/{id}/container/{cid}           delete_container()
POST   /api/game/{id}/container/{cid}/extend    extend_container()
```

## AnswerResult mapping

| GZCTF status   | `correct` | `terminal` | `kind`        | What supervisor does |
|---|---|---|---|---|
| `Accepted`     | True   | True | `accepted`   | mark complete, stop |
| `WrongAnswer`  | False  | True | `wrong`      | freeze challenge (1-strike) |
| `CheatDetected`| False  | True | `cheat`      | disable global submit |
| `NotFound`     | None   | True | `not_found`  | freeze with platform_not_found |
| `FlagSubmitted`| None   | False| `pending`    | keep polling on next tick |

## Submit payload mode

Configured at adapter construction via `submit_payload_mode`:

- `plaintext`   POST `{"flag": "..."}` directly. Default for testing.
- `encrypted`   stub — raises with `NEEDS_REAL_INSTANCE_VALIDATION`.
                Reserved for deployments that require
                `encryptApiData(payload, apiPublicKey)` per the GZCTF
                frontend.
- `auto`        Try plaintext, log a warning if response hints at
                encryption requirement (`encryptApiData` / `需要加密`).

5/9 rehearsal task: confirm the real mode against the GZCTF test
instance.  Update `configs/ai_contest.yaml` `gzctf.submit_payload_mode`
before contest start.

## Cookies / session

- `cookie_jar_path` (default `state/gzctf_cookies.json`) persists
  cookies across supervisor restarts.
- Session is held inside the adapter; every request reuses it.
  `download_attachment()` reuses the same session so authenticated
  attachments work.

## Scope guard

Every URL goes through `assert_url_in_scope()`:

- Adapter base URL is checked at `__init__`.
- Every request URL is re-checked.
- Attachment download URL (relative or absolute) is re-checked.

If `scope.allowed_domains` doesn't list the GZCTF host, every call
fails with `ScopeError` before the network is touched.  Prevents
accidental cross-domain flag submission.

## Redaction

- Password never logged.
- Cookie values never logged.
- Flag content redacted to `flag{abc…xyz}` style in logs.
- Login error message strips the request body before raising.

## Common debug steps

1. **`ScopeError`** — append the GZCTF base host to
   `configs/ai_contest.yaml` `scope.allowed_domains`.
2. **Login HTTP 401** — verify `.env` `GZCTF_USERNAME` /
   `GZCTF_PASSWORD`.  The error message is sanitized; check the
   response status itself.
3. **Submit returns no `submitId`** — older deployments wrap response
   inside a JSON object; the adapter tries `submitId`/`submit_id`/`id`.
   If still null, log raw response, set `_extract_submit_id()` to
   handle the new shape, add a test.
4. **All status polls return `FlagSubmitted`** — check
   `gzctf.poll_timeout_s` in config; the contest deployment may need
   longer.  Do NOT resubmit; the supervisor's dedup will reject it
   anyway.
5. **`encryptApiData` hint warning in logs** — flip
   `submit_payload_mode` to `encrypted`, supply `api_public_key` from
   the real frontend's config endpoint, and re-run.  If encryption is
   not yet implemented, this remains an open item logged as
   `NEEDS_REAL_INSTANCE_VALIDATION`.
