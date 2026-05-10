# subagent_request.md — sandbox cid 7

To: crypto-attack subagent
Scope: clean_solve/7/

## Task

Recover `p, q` from `hint = int(D*sqrt(p) + D*sqrt(q))` plus the
public `D, n` in `extracted/output.txt`, then decrypt the
Goldwasser–Micali-style bit list `c` to recover the flag.  If you
cannot, return `NO_CANDIDATE`.

## Inputs

- `extracted/1337crypt.sage`
- `extracted/output.txt`

## Allowed

- Pure CPython, decimal, or any high-precision integer library
  available.
- Lattice reduction libraries if importable.

## Forbidden

- Reads outside `clean_solve/7/`.
- Submission.
- Returning a guess.

## What I want back

Either:

1. The decrypted plaintext flag, plus a derivation showing how `p`
   was recovered (specifically: which p+q candidate produced a
   perfect-square discriminant of `t^2 - (p+q)t + n`), and a sanity
   check that the recovered prime passes a Fermat / Miller-Rabin
   probe; or
2. `NO_CANDIDATE` with the actual probe steps recorded in
   `evidence/attack_attempt.txt`, including a precision argument
   for why `p+q` cannot be pinned exactly.

## Hard rule

Wrong guess burns the lab's freeze slot.  Prefer NO_CANDIDATE.
