# Multivariate Coppersmith Helper Source Note

Stable entry point:

```sage
load("tools/ctf_helpers/crypto/multivariate_coppersmith.sage")
roots = small_roots(f, bounds=(X1, X2), m=2, d=2)
```

The entry point loads the vendored Sage helper from:

```text
tools/sage-env/share/coppersmith/coppersmith_patched.sage
```

## Provenance

| Field | Value |
|---|---|
| Name | `coppersmith.sage` (`defund/coppersmith`) |
| Source | <https://github.com/defund/coppersmith> |
| Commit pinned | `0b1b5ea18d9dc23142347893959dea33e7afaefe` |
| Original vendor path | `tools/sage-env/share/coppersmith/coppersmith.sage` |
| Runtime-loaded path | `tools/sage-env/share/coppersmith/coppersmith_patched.sage` |
| Upstream README path | `tools/sage-env/share/coppersmith/README.md` |
| License note | Upstream repo has no `LICENSE` file. Keep source URL and commit pin with this helper for attribution. |

## Local Compatibility Delta

The runtime entry point loads `coppersmith_patched.sage`, which preserves
defund's `small_roots(f, bounds, m=1, d=None)` API and applies a Sage 10.8
compatibility fix. The original line normalizing the polynomial by division
can route through a fraction field over `Zmod(N)`, which Sage 10.8 rejects
when `N` is composite. The patched file multiplies by
`leading.inverse_of_unit()` instead, staying inside the polynomial ring.

Integrity hashes captured on 2026-05-09:

```text
5775494ff266198b007b86850340a632ec0849b9271dbb30e2b09d957d35b111  tools/sage-env/share/coppersmith/coppersmith.sage
9720eb543ab0138776eba67b4759d1f540184893508829c143f700bc2665fc15  tools/sage-env/share/coppersmith/coppersmith_patched.sage
cdb5ceadd5690eff4c0d253b6632e911404a645a62cb50be1802fbac4a50f1da  tools/sage-env/share/coppersmith/README.md
```
