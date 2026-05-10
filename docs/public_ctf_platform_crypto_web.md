# Crypto/Web Public CTF Platform Bundle

## Scope

- Owner: worker-B.
- Challenge set: 4 static public challenges, Crypto 2 and Web 2.
- Write area: `artifacts/public-ctf-platform/crypto-web/`, `artifacts/challenges/public-cw-*/`, `docs/public_ctf_platform_crypto_web.md`.
- No real DLUT/GZCTF access, no public submissions, no runtime secrets.

## Challenge Table

| id | title | category | source_url | attachment_path | expected_flag_source |
|---|---|---|---|---|---|
| public-cw-1 | 1337crypt v2 (DownUnderCTF 2021) | Crypto | https://github.com/cryptohack/ctf_archive/tree/main/DUCTF2021_1337crypt-v2 | `artifacts/challenges/public-cw-1/attachment/1337crypt-v2.zip` | Derived locally from `description.yml` `base64_flag` in the public archive |
| public-cw-2 | 1337crypt (DownUnderCTF 2020) | Crypto | https://github.com/cryptohack/ctf_archive/tree/main/DUCTF2020_1337crypt | `artifacts/challenges/public-cw-2/attachment/1337crypt.zip` | Derived locally from `description.yml` `base64_flag` in the public archive |
| public-cw-3 | babytrick (HITCON CTF 2016) | Web | https://github.com/orangetw/My-CTF-Web-Challenges/tree/master/hitcon-ctf-2016/babytrick | `artifacts/challenges/public-cw-3/attachment/babytrick-source.zip` | Read directly from the public `config.php` `$FLAG` assignment |
| public-cw-4 | Giraffe's Coffee (HITCON CTF 2015) | Web | https://github.com/orangetw/My-CTF-Web-Challenges/tree/master/hitcon-ctf-2015/giraffe%27s-coffee | `artifacts/challenges/public-cw-4/attachment/giraffes-coffee-source.zip` | Read directly from the public `config.php` `$FLAG` assignment |

## Bundle Layout

Each `artifacts/challenges/public-cw-*/` directory contains:

- `challenge.json`
- `attachment/`
- `solver_scope.txt`
- `codex_notes.md`
- `codex_candidates.json`
- `evidence/`

`challenge.json` is coordinator-only and includes `expected_flag`. The solver must not read it.

## Solver Isolation

The only allowed solver scope is the owning `artifacts/challenges/public-cw-<n>/` directory.
That keeps expected flags out of the solver path while still letting the local GZCTF
coordinator import the flag from `challenge.json`.

## Coordinator Fields

When the local platform coordinator creates a challenge, it needs:

- `title`
- `category`
- `description`
- `source_url`
- `expected_flag`
- `expected_flag_source`
- `attachment_relpath`

Extra GZCTF metadata such as score, tags, hidden state, and attachment-download settings
can be added by the coordinator if required by the local platform UI.

## Import Readiness

- `public-cw-1`: ready
- `public-cw-2`: ready
- `public-cw-3`: ready
- `public-cw-4`: ready

## Checks

- `codex_candidates.json` is validator-compliant for all four bundles.
- Evidence paths stay inside each challenge directory.
- No `.secrets/`, `state/`, or `logs/` files were read.
