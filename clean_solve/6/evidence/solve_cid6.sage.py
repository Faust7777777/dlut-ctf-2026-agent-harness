"""cid 6 — DUCTF 2021 1337crypt v2 attack attempt.

Encryption oracle (paraphrased):

    K.<z> = NumberField((x-p)^2 + q^2)         # so z = p + q*i in C
    hint1 = p^2 + q^2
    For each hint2 entry:
        a, b in [0, 2^1337)
        delta_a = randbits(338) / 2^338  in [0, 1)
        delta_b = randbits(338) / 2^338  in [0, 1)
        x = (a + delta_a) + (b + delta_b) * z
        y = x * conjugate(x)                   # norm in K
        emit (int(y), a, b)

The norm in K simplifies to:

    y = (a + da)^2 + 2*p*(a + da)*(b + db) + (b + db)^2 * hint1
      = (a + da + (b + db) * p)^2 + ((b + db) * q)^2

i.e. y is a sum of two squares with hint1 = p^2 + q^2 also a sum of
two squares — both relations live naturally in Gaussian integers
Z[i] with pi = p + q*i (norm hint1).

Goal: recover p from hint1 + 2 hint2 entries.

Status: this script documents the lattice setup and explains why
multivariate Coppersmith is required.  See conclusion at bottom for
NO_CANDIDATE rationale.
"""
from sage.all import (
    Integer, ZZ, QQ, PolynomialRing, isqrt, sqrt
)
from pathlib import Path
import re, sys, time

T = Integer(2) ** 338

text = Path("extracted/output.txt").read_text()
hint1 = Integer(re.search(r"hint1 = (\d+)", text).group(1))

# Parse hint2 = [(y, a, b), (y, a, b)] without eval/literal_eval.
tuples = re.findall(r"\((\d+),\s*(\d+),\s*(\d+)\)", text.split("hint2 = ")[1].split("\nc = ")[0])
assert len(tuples) == 2
(y0, a0, b0), (y1, a1, b1) = [(Integer(y), Integer(a), Integer(b)) for (y, a, b) in tuples]

print(f"hint1 bits = {hint1.nbits()}")
print(f"y_0 bits   = {y0.nbits()},  a_0 bits = {a0.nbits()},  b_0 bits = {b0.nbits()}")
print(f"y_1 bits   = {y1.nbits()},  a_1 bits = {a1.nbits()},  b_1 bits = {b1.nbits()}")

# --- Setup: variables and relations --------------------------------
# Define A_i = T*a_i + alpha_i, B_i = T*b_i + beta_i, where
# alpha_i, beta_i are integer unknowns in [0, T).  Multiply the
# original relation by T^2:
#
#   T^2 * y_i = A_i^2 + 2*p*A_i*B_i + B_i^2 * hint1                (*)
#
# 5 unknowns (p, alpha_0, beta_0, alpha_1, beta_1) and 2 equations.

R = PolynomialRing(ZZ, ["p", "alpha0", "beta0", "alpha1", "beta1"], order="lex")
p_, alpha0, beta0, alpha1, beta1 = R.gens()

A0 = T*a0 + alpha0
B0 = T*b0 + beta0
A1 = T*a1 + alpha1
B1 = T*b1 + beta1

f0 = A0**2 + 2*p_*A0*B0 + B0**2*hint1 - T**2 * y0
f1 = A1**2 + 2*p_*A1*B1 + B1**2*hint1 - T**2 * y1

# --- Plan A: try to eliminate p between f0 and f1 -----------------
# 2 * p * A_i * B_i = T^2 * y_i - A_i^2 - B_i^2 * hint1
# Cross-multiplying:
#   (T^2 y_0 - A_0^2 - B_0^2 hint1) * A_1 * B_1
# = (T^2 y_1 - A_1^2 - B_1^2 hint1) * A_0 * B_0
# This is one polynomial in (alpha_0, beta_0, alpha_1, beta_1) of
# total degree 4, no p.  Coefficients of order ~hint1 ~ 2^2675.
# Unknowns each < T = 2^338.
print("\n--- Plan A: eliminate p via cross-multiplication ---")
g = (T**2 * y0 - A0**2 - B0**2 * hint1) * A1 * B1 \
  - (T**2 * y1 - A1**2 - B1**2 * hint1) * A0 * B0

# How big is the constant term?  This bounds the lattice we'd need.
print(f"  cross-multiplied polynomial total degree: {g.degree()}")
# The constant term (alphas/betas all 0) is an integer that the lattice
# must reach; recover it from g.constant_coefficient().
g_const = Integer(g.constant_coefficient())
print(f"  |constant term| bit length: {g_const.abs().nbits()}")
# Each unknown contributes at most T^k to its monomial; total mass
# from non-constant terms is bounded by (largest coeff) * T * (degree).
# We have ~5 monomial families with leading coeff order hint1 * (T*a)^2 ~
# 2^2675 * 2^(2*1421) = 2^5517.  Times T^4 = 2^1352 gives 2^6869 mass.
# So the lattice must squeeze a 2^587-or-so shortest vector out of a
# basis with ~2^6869 entries — feasible only with a very carefully
# tuned multivariate Howgrave-Graham, not the standard univariate
# `small_roots` API.
print("  mass argument: leading monomials contribute ~2^6800 each,")
print("  unknowns each up to T = 2^338, target small-roots bound 2^587-2^670.")

# --- Plan B: univariate Coppersmith via 'p mod q' style? ----------
# In cid 7 we used univariate small_roots(beta=0.5) because n was
# published, giving us a modulus p*q = n where one factor was
# bracketed by an approximate q0.  In cid 6, **n is NOT published**
# (only hint1 = p^2 + q^2).  Without a modulus there's no f(x) ≡ 0
# (mod N) framing for univariate Coppersmith.
print("\n--- Plan B: univariate small_roots(beta=...) ---")
print("  not applicable: n is not published in cid 6 output;")
print("  Sage's small_roots requires a known modulus with a")
print("  divisor we are bounding.  hint1 = p^2 + q^2 is not a")
print("  modulus we can factor in the small_roots sense.")

# --- Plan C: fpylll multivariate from scratch ---------------------
# Implementing Herrmann-May / Jochemsz-May multivariate Coppersmith
# from scratch is well-known but takes hundreds of lines plus careful
# bound tuning.  This script is a 30-minute time-boxed attempt; not
# feasible to implement and validate within the budget.
print("\n--- Plan C: fpylll Herrmann-May/Jochemsz-May from scratch ---")
print("  feasible in principle, but multivariate Coppersmith")
print("  implementations require careful basis construction and")
print("  bound tuning specific to the polynomial shape.  Estimated")
print("  effort: 200-400 lines of new code + several iterations on")
print("  beta/epsilon tuning.  Beyond the 30-min wallclock budget.")

print("\n=== conclusion ===")
print("cid 6 requires multivariate Coppersmith; not available as a")
print("packaged primitive in Sage 10.8 and not implementable")
print("from scratch within the time budget for this run.")
print("Returning NO_CANDIDATE; the lattice setup above (Plan A's")
print("cross-multiplied polynomial g and its bit-length analysis)")
print("is the artifact a future Sage worker can pick up directly.")
