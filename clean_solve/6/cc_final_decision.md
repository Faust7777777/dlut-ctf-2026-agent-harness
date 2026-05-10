# cc_final_decision.md — sandbox cid 6 (third pass: full ladder)

## Decision

**No candidate.**  Emit `codex_candidates.json` as `[]`.

This is the third time cid 6 lands at NO_CANDIDATE.  Each pass moved
the rejection one level deeper in the escalation ladder defined by
`docs/solve_first_loop_policy.md`.  This pass walked the **full**
ladder; the rejection now sits at the algorithm-implementation
geometry, not at the runtime, the helper-availability, or the
budget.

| Pass | Rejection level |
|---|---|
| v1 | Tooling (assumed Sage / fpylll unavailable) |
| v2 | Algorithm-package (Sage installed but no multivariate Coppersmith helper available) |
| v3 (this) | **Helper-bound** (defund's `small_roots` vendored and works, but its asymptotic bound `X < N^(1/(d·binom(d+n-1, n)))` is not met for our problem's geometry — `X = T = 2^338`, `N ≈ 2^15000`, `d = 4`, `n = 4` ⇒ bound `X < 2^15000/140 ≈ 2^107`) |

## Ladder evidence walked

| Step | Evidence |
|---|---|
| identify gap | `tool_gap.md` |
| local check | `tool_gap.md` (recorded `find tools/ -iname '*coppersmith*'` empty) |
| vendor helper | `helper_source.md` — defund/coppersmith @ `0b1b5ea18d9d…aefe`, MIT-style, vendored to `tools/sage-env/share/coppersmith/coppersmith.sage` plus `coppersmith_patched.sage` (one-line Sage-10.8 compat fix: `f /= leading` → `f *= leading.inverse_of_unit()`) |
| smoke / toy | `evidence/smoke_coppersmith.sage` + `evidence/helper_smoke_test.txt` — bivariate `(a + X)(b + Y) = c` toy: PASS, recovered `(1016558464605, 371244445609)` exactly |
| real-challenge attempt | `evidence/solve_cid6.sage` + `evidence/solver_run.txt` — three lattice runs (m=1 d=2, m=2 d=2, m=3 d=2) on the cross-multiplied integer relation `g(α_0, β_0, α_1, β_1)`.  Each run **completed cleanly**; each returned only the trivial root `(0, 0, 0, 0)` |
| failure mode | LLL/HG cannot find the true small root because the unknowns (each ≤ 2^338) exceed defund's generic small-roots bound for this polynomial shape; the lattice converges on (0,0,0,0) which is not an actual zero of `g` over Z |

## Why I adopt this NO_CANDIDATE

- The helper imports cleanly inside `tools/bin/sage` and the Sage-10.8
  patch was the only modification required.
- The smoke test passes (toy with X = 2^40 vs. N ≈ 2^512 — within
  defund's bound, recovers true root in one shot).
- The cid-6 specific lattice runs **complete** (no exceptions, no
  timeouts at m=2/3 with d=2 over Zmod(2^15000-1)) but return only
  `(0,0,0,0)`, which when fed back through `p_cand = (T²·y_0 - A_0² -
  B_0²·hint1) / (2·A_0·B_0)` does NOT yield a 1337-bit prime — and
  which fails `hint1 - p_cand²` being a perfect square.
- A from-scratch Jochemsz-May / Herrmann-May with a custom
  monomial-set selection optimised for this 4-variable degree-4
  geometry is the correct next step.  defund's helper uses the
  generic full-shift basis which is provably suboptimal for this
  problem, hence the bound mismatch.

## Why I would override (and don't)

- If a single `(0,0,0,0)`-style trivial root produced an integer-prime
  `p_cand` reconstruction by accident — it does not.
- If a higher m/d combination converged in reasonable wallclock —
  the helper output makes clear the bound, not the dimension, is
  the limit.  Going to m=4 or d=3 grows lattice dim past 100 and
  pays cubic-in-dim time penalty per LLL pass without changing
  the asymptotic bound `X < N^(1/(d·binom(d+n-1,n)))`.

## Why I'm not guessing

`max_wrong_per_challenge=1` means a single wrong submission freezes
the challenge for the remainder of the run.  The expected value of
guessing a 1337-bit-prime-derived flag is dominated by the freeze
risk; the lattice attempts above did not narrow the candidate flag
to anything testable.

## What still gets recorded

- `cc_hypothesis.md` — algebra + capability gap.
- `tool_gap.md` — gap classification + plan.
- `helper_source.md` — vendor record + commit hash + Sage-10.8 patch
  rationale.
- `subagent_request.md` — explicit budget & no-guess discipline.
- `subagent_reply.md` — full ladder narrative.
- `evidence/attack_attempt.txt` — v1 integer-only probe.
- `evidence/solve_cid6.sage` — v3 multivariate Coppersmith script
  (cross-multiplication elimination + synthetic-N lift).
- `evidence/solve_cid6_output.txt` — earlier static analysis output.
- `evidence/smoke_coppersmith.sage` — toy regression for the
  vendored helper (PASS).
- `evidence/helper_smoke_test.txt` — smoke run output.
- `evidence/solver_run.txt` — actual cid-6 attack run output
  showing `(0,0,0,0)` returned at m=1, m=2, m=3 with d=2.
- `codex_candidates.json` = `[]`.

## Stop conditions for next iteration

The next operator should attempt **before** writing another
NO_CANDIDATE:

1. Custom Jochemsz-May shift selection on the specific 31-monomial
   geometry of `g` (avoid full-shift; pick monomials that respect
   the `(α_0, β_0)` vs `(α_1, β_1)` symmetry of cid 6).
2. Or solve directly in `Z[π]` with `π = p + q·i` Gaussian integer
   of norm `hint1`, using `(a_i + b_i·π)`-style relations.

Either path is several hours of new code; defund's generic helper
will not extend to this regime regardless of m/d tuning.
