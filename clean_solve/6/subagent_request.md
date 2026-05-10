# subagent_request.md — sandbox cid 6 (second pass)

To: crypto-lattice subagent
Scope: clean_solve/6/
Wallclock budget: 30 minutes

## Task

The lab's solver runtime has been unified to `tools/bin/sage-python`
which now exposes Sage 10.8 + fpylll + mpmath + sympy + pycryptodome
(verified via `state/runtime_capabilities.json:crypto_lattice = true`).
With those tools available, attempt to recover `p, q` from the
hint structure in `extracted/output.txt`, then decrypt the
ciphertext over `Z/nZ[I]/(I^2 + 1)`.  If you cannot within the
budget, return `NO_CANDIDATE` with the lattice setup recorded.

## Inputs

- `extracted/1337crypt-v2.sage`
- `extracted/output.txt`

## Allowed

- `tools/bin/sage-python` for any Sage / fpylll / mpmath / pycrypto
  operation.
- Custom lattice construction via fpylll if needed.

## Forbidden

- Reads outside `clean_solve/6/`.
- Submission anywhere.
- Returning a guessed flag.

## What I want back

Either:

1. The decrypted plaintext flag, plus a derivation script in
   `evidence/solve_cid6.sage.py` that another worker can re-run,
   plus a sanity probe (e.g., `p * q != 0`, `p^2 + q^2 == hint1`,
   `p, q` both prime); or
2. `NO_CANDIDATE` with `evidence/solve_cid6.sage.py` documenting
   why each of the obvious paths (univariate Coppersmith,
   Groebner elimination, Gaussian-integer norm matching) fails
   for **this specific oracle**, including the published algebraic
   structure of the cid 6 hints.  The orchestrator must be able
   to read that file and understand whether to retry with a
   bigger time budget or whether the algorithm is fundamentally
   missing.

## Hard rules

- Budget: 30 minutes wallclock. Hit the timer → write
  NO_CANDIDATE, don't keep extending.
- A wrong guess burns the lab's freeze slot. Always prefer
  NO_CANDIDATE.
- Reject any plan that requires reading data outside
  `clean_solve/6/`.
