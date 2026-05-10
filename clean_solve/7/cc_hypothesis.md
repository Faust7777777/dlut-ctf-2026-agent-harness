# cc_hypothesis.md — sandbox cid 7

## Sandbox state

`clean_solve/7/` opened with one zip.  Unzip into `extracted/` gives:

- `1337crypt.sage` (~30 lines, encryption oracle)
- `output.txt` (cipher state: hint, D, n, c)

I worked only inside this sandbox.

## Encryption scheme (paraphrased from the source)

The script generates 1337-bit primes `p, q`, sets `n = p*q`, picks
a small constant `D = 63^14` (~84 bits), and publishes:

- `hint = int(D*sqrt(p) + D*sqrt(q))`
- `D`, `n`
- `c = [...]`: a list of integers, one per plaintext bit.

The encryption is essentially Goldwasser–Micali, choosing a public
`x` with `(x|p) = (x|q) = -1` so x is a non-residue mod n.  Each
plaintext bit `b` is encrypted as `c = x^(1337 + b) * r^(2*1337) mod n`:

- bit `b = 0` → `c = x^1337 * r^2674` → since 1337 is odd and x is
  a non-residue mod n, the QR-character equals (-1)^1337 = -1 →
  c is a non-residue mod n.
- bit `b = 1` → `c = x^1338 * r^2674` = `(x^2)^669 * r^2674` is QR
  mod n.

Decryption: with p, q known, compute Legendre symbols modulo each
prime; bit = 1 iff c is QR mod n.

## What it would take to recover the flag

1. Recover `p, q` from `hint`, `D`, `n`.
2. For each ciphertext entry `c_i`, compute `bit_i = (c_i is QR mod n)`.
3. Concatenate bits as a big-endian integer; convert to bytes via
   `bytes_to_long`-inverse; UTF-8 decode.

Step 1 is the bottleneck.

## Updated hypothesis after solver runtime unification

The original precision audit was right that brute force is impossible:
`p+q` lives in a roughly `2^587`-wide interval.  But that width is below
the univariate Coppersmith threshold for an approximate factor of a
2674-bit RSA modulus.

Use the lower-bound estimate:

`s0 = round((hint/D)^2 - 2*sqrt(n)) ~= p+q`

Then derive a close approximate larger factor:

`q0 = (s0 + isqrt(s0^2 - 4n)) // 2`

The true larger factor is `q = q0 + x` for small `x`.  Since `q` divides
`n`, solve `(q0 + x) == 0 mod q` with Sage's univariate Coppersmith via
`(x + q0).small_roots(X=2^600, beta=0.5, epsilon=0.04)`.

This recovers a verified factor pair `p*q == n`.  Decrypt each GM bit
with Legendre symbols: QR modulo both primes maps to plaintext bit 1;
non-residue modulo both primes maps to plaintext bit 0.

## Plan

Emit the recovered DUCTF candidate with high confidence and evidence
pointing to the solve script plus raw run output.

## Evidence to record

- `extracted/1337crypt.sage`
- `extracted/output.txt`
- `evidence/attack_attempt.txt`
- `evidence/solve_cid7.py`
