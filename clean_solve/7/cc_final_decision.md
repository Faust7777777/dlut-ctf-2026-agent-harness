# cc_final_decision.md — sandbox cid 7

## Decision

Emit a high-confidence candidate recovered with Sage Coppersmith and
Goldwasser-Micali Legendre-symbol decryption.

## Why I override the old NO_CANDIDATE

- The old precision audit correctly showed brute force was impossible,
  but the newly unified solver runtime exposes Sage and fpylll.
- `evidence/solve_cid7.py` recovers a verified factor pair with
  univariate Coppersmith: `p*q == n`.
- The decrypted bytes are valid UTF-8 and match the challenge's DUCTF
  flag format.

## Why I would override (and don't)

I would override if:
I would reject if:
- the factor pair did not multiply back to `n`;
- the decrypted bytes were not valid UTF-8;
- the candidate did not match the current challenge's flag format.

## What still gets recorded

- `cc_hypothesis.md` — the GM-style decryption math.
- `subagent_request.md` — the explicit "no guessing" rule.
- `subagent_reply.md` — both the integer-only probe and the
  precision audit.
- `evidence/attack_attempt.txt` — raw probe output.
- `codex_candidates.json` contains the recovered candidate.
