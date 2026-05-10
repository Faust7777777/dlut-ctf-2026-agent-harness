# Local GZCTF Acceptance Note

## Target

Local-only rehearsal of `GZCTFAdapter + supervisor + FlagGuard`.

## Covered locally

- login, profile, team, game, game details, challenge detail
- attachment download
- submit and status polling
- container create, extend, delete
- static attachment challenges for Misc, Forensics, Crypto, and Reverse
- dynamic container challenges for Pwn and Web

## Verified evidence

- URL: `http://127.0.0.1:8080`
- Image: `gztime/gzctf:latest`
- Game: `1`
- Challenge IDs:
  - `1` `misc-static`
  - `2` `pwn-dynamic`
  - `3` `forensics-static`
  - `4` `crypto-static`
  - `5` `reverse-download`
  - `6` `web-dynamic`
  - `7` `web-duplicate-hold`
- Accepted: challenges `1` and `3`
- WrongAnswer freeze: challenge `4`
- Heartbeat/state/log: present in `logs/local-gzctf/*.jsonl` and `state/local-gzctf/ai_contest_state.json`

## Mock-only or still conditional

- alternate status envelopes beyond the live local instance
- encrypted submit payload mode
- any non-localhost platform behavior
- real DLUT SSO / campus-specific auth flows
- `duplicate_candidate_skipped` is still only covered as an intended path in the supervisor/mock layer; the live local GZCTF rehearsal did not emit it stably.

## Known local coordinates

- URL: `http://127.0.0.1:8080`
- Image: `gztime/gzctf:latest`
- Game: `1`
