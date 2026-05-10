# cc_hypothesis.md — sandbox cid 6 (second pass)

## Sandbox state

`clean_solve/6/` opened with one zip.  Unzip into `extracted/` gives
two siblings:

- `1337crypt-v2.sage` (606 bytes, the encryption oracle)
- `output.txt` (7285 bytes, the cipher's published state)

I worked only inside this sandbox.

## What the source does

The script generates 1337-bit primes `p, q`, sets `n = p*q`, and
constructs the number field `K = Q[z] / ((x - p)^2 + q^2)`.  Two
"hint" outputs are published:

- `hint1 = p^2 + q^2` (a single ~2675-bit integer).
- `hint2`: a list of 2 entries `(int(y_i), a_i, b_i)` where
  `a_i, b_i ∈ [0, 2^1337)` and
  `y_i = (a_i + δa_i)^2 + 2 p (a_i + δa_i)(b_i + δb_i) + (b_i + δb_i)^2 hint1`
  with `δa_i, δb_i ∈ [0, 1)` of size `~2^-338` (i.e. of the form
  `randbits(338)/2^338`).

Encryption: `m = r + flag·I` where `I^2 = -1`, then
`c = m^0x1337` in the ring `Z/nZ[I]/(I^2+1)`.

**Importantly**: `n` is NOT printed in `output.txt` (only hint1, hint2,
and c).  This rules out univariate Coppersmith via `small_roots(beta=…)`
because Sage's API needs a modulus with a known factor structure.

## Updated hypothesis after solver runtime unification

The lab now has Sage 10.8 + fpylll (verified by
`runtime_capabilities.json`), so the previous "no Sage" justification
is gone.  But **the obstacle for cid 6 is not Sage — it's algorithmic**:

- The two hint relations have 5 unknowns (p, α_0, β_0, α_1, β_1).
- Eliminating p between them gives one degree-4 polynomial in
  (α_0, β_0, α_1, β_1) with constant-term magnitude ~2^8027 and
  bound `|α|, |β| < T = 2^338` per unknown.
- This is a textbook **multivariate Coppersmith** instance
  (Herrmann-May / Jochemsz-May).  Sage 10.8 does NOT ship a packaged
  primitive for it — only univariate `small_roots` over a single
  polynomial mod a known modulus.
- A from-scratch fpylll lattice (200-400 lines + bound tuning) is
  feasible in principle but beyond the 30-minute wallclock budget
  for this attempt.

## Plan

1. Subagent: run `evidence/solve_cid6.sage.py` to confirm:
   - the algebra (norm in K reduces correctly to the published form);
   - Plan A (cross-multiplication elimination of p) produces the
     expected degree-4 polynomial in the four δ-unknowns;
   - Plan B (Sage univariate small_roots) is **not applicable**
     because n is not published, so there is no f(x) ≡ 0 (mod N)
     framing to feed `small_roots(beta=…)`;
   - Plan C (custom multivariate Coppersmith via fpylll) is feasible
     in principle, infeasible within the budget.
2. Persist the lattice setup + reasoning to
   `evidence/solve_cid6_output.txt`.
3. Decline (NO_CANDIDATE).  This time the decline is honest at a
   different level than v1: tools are available, but the algorithm
   to exploit them isn't packaged and the implementation budget is
   exceeded.

## Evidence to record

- `extracted/1337crypt-v2.sage`
- `extracted/output.txt`
- `evidence/attack_attempt.txt` (v1 integer-only probe)
- `evidence/solve_cid6.sage.py` (v2 lattice setup)
- `evidence/solve_cid6_output.txt` (Sage run output)

## Confidence

This is a **deliberate rejection** at the algorithm-implementation
level, not at the tooling level.  A future operator can pick up
`solve_cid6.sage.py` and continue from Plan C with about half a day
of LLL bound tuning.
