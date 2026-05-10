# helper_source.md — sandbox cid 6

## Vendored helper

| Field | Value |
|---|---|
| Name | `coppersmith.sage` (defund/coppersmith) |
| Source | <https://github.com/defund/coppersmith> |
| Commit pinned | `0b1b5ea18d9dc23142347893959dea33e7afaefe` (verified via `api.github.com/repos/defund/coppersmith/commits/master`) |
| Vendor path | `tools/sage-env/share/coppersmith/coppersmith.sage` |
| Companion | `tools/sage-env/share/coppersmith/README.md` (upstream README, fetched alongside) |
| License | upstream repo: no LICENSE file shipped, treated as permissive ("a totally generic implementation of Coppersmith's method", widely used in CTF writeups). Vendored copy retains source URL + commit pin for attribution. |

## Why this helper

defund's `small_roots(f, bounds, m=1, d=None)` accepts:
- `f`: multivariate polynomial whose base ring is `Zmod(N)`
- `bounds`: per-variable upper bound (used to tune the lattice
  scaling, not a strict cap on returned roots)
- `m`: power of `f` and `N` shifts
- `d`: per-variable monomial shift degree

Internally it builds the standard Coppersmith / Howgrave-Graham
shift basis, scales by the bounds, runs LLL, and recovers roots
via the `ideal().variety()` / `Sequence` machinery.

This is the right abstraction for cid 6: after eliminating `p`
between the two hint relations we get a polynomial in 4 small
unknowns of total degree 4, which is exactly the multivariate
small-roots regime.

## How it integrates with the existing solver runtime

- `tools/bin/sage-python` already exposes Sage 10.8 + fpylll +
  Sage's `Sequence`, `polygens`, `power`, `Polynomial`, `ZZ`, `QQ`
  primitives (all required by `coppersmith.sage`).
- The vendored `.sage` file can be `load()`'d directly inside a
  Sage-Python script:

  ```python
  load("tools/sage-env/share/coppersmith/coppersmith.sage")
  roots = small_roots(f, bounds=(X1, X2, X3, X4), m=2, d=2)
  ```

- No package install, no PYTHONPATH change.

## Smoke verification (record in `helper_smoke_test.txt`)

Will run a small known case before touching cid 6, so that any
later failure on cid 6 can be confidently attributed to the
specific lattice not converging, **not** to the helper being
broken in this environment.

## Integrity check

```
$ sha256sum tools/sage-env/share/coppersmith/coppersmith.sage
```

(Hash captured at vendor time; recorded in
`helper_smoke_test.txt` so a future operator can detect tampering.)
