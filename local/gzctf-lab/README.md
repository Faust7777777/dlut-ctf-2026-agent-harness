# Local GZCTF Lab

This directory holds the localhost-only rehearsal stack for `GZCTFAdapter + supervisor + FlagGuard`.

## Defaults

- URL: `http://127.0.0.1:8080`
- Image: `gztime/gzctf:latest`
- DB image: `postgres:alpine`
- Game ID: `1`
- Seeded challenge IDs:
  - `1` `misc-static`
  - `2` `pwn-dynamic`
  - `3` `web-dynamic`
  - static IDs are assigned by the seed script for `forensics-static`, `crypto-static`, and `reverse-download`

## Commands

```bash
bash local/gzctf-lab/scripts/start.sh
bash local/gzctf-lab/scripts/seed.sh
bash local/gzctf-lab/scripts/verify.sh
bash local/gzctf-lab/scripts/reset.sh
```

## What seed does

- waits for the local GZCTF API
- updates the game window so submissions stay open
- ensures the admin, player, team, and accepted participation exist
- creates or refreshes static attachment and dynamic container challenges
- builds the local `web` and `pwn` challenge images

## What verify checks

- adapter login / profile / team / game / details / challenge / attachment / submit / poll
- container create / extend / delete
- supervisor healthcheck, sync, submit, heartbeat, state, and logs
- accepted, wrong-answer, and duplicate-candidate behavior

## Notes

- Everything binds to `127.0.0.1` only.
- No real secrets, domains, or contest endpoints are stored here.
- `reset.sh` clears local supervisor artifacts and destroys live challenge containers, but keeps the local seed snapshot in place.

