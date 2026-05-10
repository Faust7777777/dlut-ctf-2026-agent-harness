# solve_cid6.sage — multivariate Coppersmith attack on DUCTF 2021 1337crypt v2.
#
# Run with:
#   tools/bin/sage clean_solve/6/evidence/solve_cid6.sage
#
# Attack outline:
#   The encryption oracle leaks two relations of the form
#       T^2 * y_i = (T*a_i + α_i)^2
#                 + 2*p*(T*a_i + α_i)*(T*b_i + β_i)
#                 + (T*b_i + β_i)^2 * hint1
#   with i in {0,1}, T = 2^338, α_i, β_i in [0, T), p the 1337-bit prime.
#
#   Eliminate p between the two relations:
#       (T^2*y_0 - A_0^2 - B_0^2*hint1) * A_1*B_1
#     = (T^2*y_1 - A_1^2 - B_1^2*hint1) * A_0*B_0
#   Calling the difference g(α_0,β_0,α_1,β_1), this polynomial is
#   identically zero at the true (α_0,β_0,α_1,β_1) over Z.
#
#   defund's small_roots takes f over Zmod(N).  We pick a synthetic
#   modulus N larger than every magnitude g takes inside the box,
#   so any integer root of g is also a root mod N.  Then call
#   small_roots(g, bounds=(T,)*4, m, d) and recover the four δs.
#   With (α_0,β_0,α_1,β_1) known, reconstruct p from either hint and
#   sanity-check (p^2 + q^2 == hint1, p*q makes sense, both prime,
#   and the ciphertext decrypts to a flag-shaped string).

load("/home/wuwai/dlut-ctf-2026/tools/sage-env/share/coppersmith/coppersmith_patched.sage")

import re, ast, sys
from pathlib import Path

T = Integer(2)^338

text = open("extracted/output.txt").read()
hint1 = Integer(re.search(r"hint1 = (\d+)", text).group(1))
tuples = re.findall(r"\((\d+),\s*(\d+),\s*(\d+)\)",
                    text.split("hint2 = ")[1].split("\nc = ")[0])
assert len(tuples) == 2
(y0, a0, b0), (y1, a1, b1) = [(Integer(y), Integer(a), Integer(b)) for (y, a, b) in tuples]
import sys
print(f"hint1 bits = {hint1.nbits()}", flush=True); sys.stdout.flush()
print(f"hint2 #0: y bits={y0.nbits()} a bits={a0.nbits()} b bits={b0.nbits()}")
print(f"hint2 #1: y bits={y1.nbits()} a bits={a1.nbits()} b bits={b1.nbits()}")

# --- Build the cross-multiplied integer relation g over ZZ first ---
# Building over Zmod(N) directly would force every monomial product
# through a 9500-bit modular reduction; building over ZZ keeps the
# polynomial expansion fast, then we lift to Zmod(N) once.
RZZ = PolynomialRing(ZZ, names=("aa0", "bb0", "aa1", "bb1"), order="lex")
aa0, bb0, aa1, bb1 = RZZ.gens()

A0 = T*a0 + aa0
B0 = T*b0 + bb0
A1 = T*a1 + aa1
B1 = T*b1 + bb1

print("building g over ZZ...", flush=True)
LHS = (T^2 * y0 - A0^2 - B0^2 * hint1) * A1 * B1
RHS = (T^2 * y1 - A1^2 - B1^2 * hint1) * A0 * B0
g_ZZ = LHS - RHS
print(f"  g.degree = {g_ZZ.degree()}, #monomials = {len(g_ZZ.monomials())}", flush=True)

# Synthetic modulus: a Mersenne-style number is much faster to
# construct than next_prime(2^9500), and we don't need primality —
# small_roots only needs the leading coefficient to be a unit, which
# happens generically.  Use 2^9500 - 1 directly (composite is fine
# for the synthetic-N trick provided the leading coefficient is
# coprime to N).
print("constructing synthetic modulus N = 2^9500 - 1 ...", flush=True)
N = Integer(2)^15000 - 1
print(f"  N has {N.nbits()} bits, factor leading coef and N coprime check follows", flush=True)

R = PolynomialRing(Zmod(N), names=("aa0", "bb0", "aa1", "bb1"), order="lex")
g = g_ZZ.change_ring(Zmod(N))
print(f"  g lifted to Zmod(N) OK", flush=True)
# small_roots's `f /= f.coefficients().pop(0)` requires leading
# coefficient invertible mod N; if it isn't, abort with diagnostic.
leading = Integer(g.coefficients()[0])
gcd_lead = gcd(leading, N)
print(f"  gcd(leading_coef, N) = {gcd_lead.nbits()} bits", flush=True)
if gcd_lead != 1:
    print("  leading coef not a unit; pick a different synthetic N (try a prime)", flush=True)

# --- Run small_roots with progressively larger m,d until success ---
# Wallclock-bounded.  Lattice dimension grows fast in d^n*(m+1).
# Try increasing m to widen the small-roots bound.  Lattice dim
# grows like d^n * (m+1) so each step is significantly slower.
# m=2 d=2 -> dim ~48; m=3 d=2 -> dim ~64.
attempts = [
    (2, 2),
    (3, 2),
]
recovered = None
for (m, d) in attempts:
    print(f"\n--- attempt small_roots(m={m}, d={d}) ---")
    sys.stdout.flush(); print("...lattice running, this may take minutes...", flush=True)
    try:
        roots = small_roots(g, bounds=(T, T, T, T), m=m, d=d)
    except Exception as exc:
        print(f"  raised: {type(exc).__name__}: {exc}")
        continue
    print(f"  roots returned: {len(roots)}")
    if not roots:
        continue
    for r in roots[:5]:
        print(f"    {tuple(int(v) for v in r)}")
    # Reconstruct p from the first valid root
    for r in roots:
        alpha0_v, beta0_v, alpha1_v, beta1_v = (Integer(int(v)) for v in r)
        if any(not (0 <= v < T) for v in (alpha0_v, beta0_v, alpha1_v, beta1_v)):
            continue
        A0v = T*a0 + alpha0_v
        B0v = T*b0 + beta0_v
        num = T^2 * y0 - A0v^2 - B0v^2 * hint1
        den = 2 * A0v * B0v
        if den == 0 or num % den != 0:
            continue
        p_cand = num // den
        if p_cand <= 1 or p_cand.nbits() not in (1336, 1337, 1338):
            continue
        # check p^2 + q^2 == hint1 for integer q
        q_sq = hint1 - p_cand^2
        if q_sq < 0:
            continue
        q_cand = isqrt(q_sq)
        if q_cand^2 != q_sq:
            continue
        if not (p_cand.is_prime() and q_cand.is_prime()):
            print(f"    p_cand bits={p_cand.nbits()}, q_cand bits={q_cand.nbits()},"
                  f" both prime? {p_cand.is_prime()}/{q_cand.is_prime()}")
            continue
        recovered = (p_cand, q_cand, alpha0_v, beta0_v, alpha1_v, beta1_v)
        break
    if recovered is not None:
        break

if recovered is None:
    print("\n=== NO_CANDIDATE ===")
    print("Lattice did not return a valid small root that reconstructs"
          " a (p, q) pair satisfying p^2 + q^2 == hint1 with both prime.")
    raise SystemExit(1)

p, q, *_ = recovered
n = p * q
print(f"\n=== p, q recovered ===")
print(f"p bits = {p.nbits()}, q bits = {q.nbits()}, n bits = {n.nbits()}")

# --- Decrypt c = m^0x1337 in Z/nZ[I] / (I^2 + 1) -----------------
# The encryption produces c such that m^0x1337 = c, with
# m = r + flag*I.  Recover m by inverting 0x1337 modulo the orders
# in Z/nZ[I]/(I^2+1).
# Strategy: split the ring via CRT over p and q.
m_int_re = re.search(r"c = (.*)", text, re.S).group(1).strip()
# c = `<int>*I + <int>` form
m_match = re.match(r"(?P<imag>-?\d+)\s*\*\s*I\s*\+\s*(?P<real>-?\d+)", m_int_re)
if not m_match:
    # alternate form: real first
    m_match = re.match(r"(?P<real>-?\d+)\s*\+\s*(?P<imag>-?\d+)\s*\*\s*I", m_int_re)
if not m_match:
    print(f"could not parse c; head = {m_int_re[:120]!r}")
    raise SystemExit(2)
c_re = Integer(m_match.group("real"))
c_im = Integer(m_match.group("imag"))
print(f"c.real bits = {abs(c_re).nbits()}, c.imag bits = {abs(c_im).nbits()}")

def decrypt_mod_prime(c_re, c_im, prime, e):
    """Invert m^e in Z/prime[I]/(I^2+1) given c_re + c_im * I."""
    # I^2 = -1.  Decryption ring = GF(prime)[I]/(I^2+1).
    if power_mod(-1, (prime - 1) // 2, prime) == 1:
        # -1 is a QR mod prime: I^2+1 splits, ring = GF(p) x GF(p).
        sqrt_neg1 = mod(-1, prime).sqrt()
        s = Integer(sqrt_neg1)
        # I |-> s in component A, I |-> -s in component B
        A = Integer((c_re + c_im * s) % prime)
        B = Integer((c_re - c_im * s) % prime)
        # invert e mod (prime - 1) (since GF(prime)* is cyclic of order prime-1)
        inv_e = inverse_mod(e, prime - 1)
        mA = Integer(power_mod(A, inv_e, prime))
        mB = Integer(power_mod(B, inv_e, prime))
        # back-map: m_re = (mA + mB)/2, m_im = (mA - mB)/(2*s)
        two_inv = inverse_mod(2, prime)
        m_re = (mA + mB) * two_inv % prime
        m_im = (mA - mB) * two_inv * inverse_mod(s, prime) % prime
        return Integer(m_re), Integer(m_im)
    else:
        # -1 is non-QR mod prime: ring = GF(prime^2).
        F = GF(prime^2, name='II', modulus=PolynomialRing(GF(prime), 'X').gen()^2 + 1)
        II = F.gen()
        cF = F(c_re) + F(c_im) * II
        order = prime^2 - 1
        inv_e = inverse_mod(e, order)
        mF = cF^inv_e
        coeffs = mF.polynomial().list()
        m_re = Integer(coeffs[0]) if len(coeffs) >= 1 else Integer(0)
        m_im = Integer(coeffs[1]) if len(coeffs) >= 2 else Integer(0)
        return m_re, m_im

E = Integer(0x1337)
m_re_p, m_im_p = decrypt_mod_prime(c_re, c_im, p, E)
m_re_q, m_im_q = decrypt_mod_prime(c_re, c_im, q, E)
print(f"per-prime decrypt OK")

# CRT lift to Z/n
m_re = crt([m_re_p, m_re_q], [p, q])
m_im = crt([m_im_p, m_im_q], [p, q])
print(f"CRT lifted m_im bits = {abs(Integer(m_im)).nbits()}")

# m = r + flag * I, so flag = m_im (mod n).  Convert to bytes.
flag_long = Integer(m_im) % n
flag_bytes = flag_long.to_bytes((flag_long.nbits() + 7) // 8 or 1, "big")
print(f"recovered candidate bytes (len={len(flag_bytes)}): {flag_bytes!r}")
try:
    print(f"recovered candidate utf-8 : {flag_bytes.decode('utf-8')!r}")
except UnicodeDecodeError as exc:
    print(f"  utf-8 decode failed: {exc}")
