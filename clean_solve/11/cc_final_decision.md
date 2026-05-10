# cc_final_decision.md — sandbox cid 11

## Decision

Submit `picoCTF{1n_7h3_|<3y_of_f911a486}` as a high-confidence
candidate.

## Why I adopt the subagent's reply

- The derivation is purely SHA-256 + integer indexing; both are
  deterministic and reproducible by any Python interpreter.  I
  re-ran the same SHA-256 once more to confirm — match.
- The eight `if key[i] != hashlib.sha256(...).hexdigest()[N]`
  branches in the source spell out the index order; I read all
  eight branches and they match the subagent's
  `[4, 5, 3, 6, 2, 7, 1, 8]` list.
- The bundled source's flag template is the same string as the
  unlock key, so a valid license key is also the flag.

## Why I would override (and don't here)

I would override if:
- The subagent's `indices` list disagreed with what I find in the
  source (it does not).
- The dynamic length disagreed with `len(prefix) + len(dynamic) +
  len(suffix) == len(template)` (it does: 23 + 8 + 1 = 32 chars).
- SHA-256 of a different `username_trial` constant produced a
  different output than what was in the evidence file (it does
  not).

## Evidence

- `extracted/keygenme-trial.py`
- `evidence/keygen_derivation.txt`
