# tool_gap.md — sandbox cid 6

## Capability needed

**Multivariate Coppersmith small-roots** over a polynomial ring with
multiple integer-bounded unknowns.

cid 6's encryption oracle leaks two relations of the form

```
y_i = (T·a_i + α_i)^2  +  2·p·(T·a_i + α_i)·(T·b_i + β_i)  +  (T·b_i + β_i)^2 · hint1
```

with `i ∈ {0, 1}`, `T = 2^338`, unknowns `p` (1337-bit prime),
`α_i, β_i ∈ [0, T)`.  Eliminating `p` between the two relations
yields one degree-4 polynomial in 4 small unknowns; recovering them
is the canonical Herrmann-May / Jochemsz-May lattice problem.

## What's already on the box

| Probe | Result |
|---|---|
| `tools/bin/sage-python -c "import sage.all"` | OK (Sage 10.8) |
| `tools/bin/sage-python -c "from fpylll import LLL"` | OK |
| `runtime_capabilities.json:crypto_lattice` | `true` |
| Sage's univariate `small_roots(f, beta=…)` | available, but **needs known modulus** |
| Sage's multivariate `small_roots` packaged primitive | **NOT available in 10.8** |

So tools are present at the library level (Sage + fpylll); the gap
is one lattice-construction layer above that.

## What I checked before deciding to vendor

```bash
ls tools/sage-env/share/                      # was empty
tools/bin/sage-python -m pip list | grep -i copper    # nothing
find tools/ -iname '*coppersmith*'             # nothing
```

No local helper.  Search would be needed.

## Helper to vendor (ladder step 4)

`defund/coppersmith` (GitHub: <https://github.com/defund/coppersmith>):

- Single-file `coppersmith.sage` (~50 lines)
- Generic multivariate `small_roots(f, bounds, m, d)`
- Used in many published CTF writeups (DUCTF/HITCON/picoCTF)
- Public repo, MIT-style permissive

This matches the "minimal helper, not a framework" rule from
`docs/solve_first_loop_policy.md`.

## Smoke / toy test (ladder step 5)

Will run a known-easy bivariate small-root case (Boneh–Durfee
toy or similar) before re-attempting cid 6.  See
`helper_smoke_test.txt`.

## Original-challenge retry (ladder step 6)

Will run defund's `small_roots` against cid 6's elimination-form
polynomial, with `m, d` tuned to the 4-variable degree-4 shape.
Capture exact run output to `solver_run.txt`.

## Stop conditions

Per policy, NO_CANDIDATE only allowed if:
- helper imports OK in the sage-python interpreter
- toy smoke test passes
- the cid-6-specific lattice attack runs but fails to return a
  root, with concrete failure mode logged

If any of those steps **breaks** mid-ladder (e.g. helper crashes
on the toy), that becomes the new gap and the ladder repeats from
the appropriate step.
