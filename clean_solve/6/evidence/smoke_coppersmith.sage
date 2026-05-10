# smoke_coppersmith.sage — toy regression for the vendored helper.
#
# Run with:
#     tools/bin/sage clean_solve/6/evidence/smoke_coppersmith.sage
#
# Goal: verify that loading defund's helper inside this Sage 10.8
# install actually recovers a known small root, so that any later
# failure on cid 6 is attributable to the cid-6-specific lattice
# rather than to the helper being broken in this environment.
#
# Toy: bivariate small-roots over Zmod(N).  Construct N = p*q from
# two random 256-bit primes, pick small random unknowns (x, y) in
# a known bound, build f(X, Y) = (a + X)(b + Y) - c with the true
# c = (a + x)(b + y), and ask `small_roots` to recover (x, y).
# This is the textbook smoke test for defund's small_roots.

load("/home/wuwai/dlut-ctf-2026/tools/sage-env/share/coppersmith/coppersmith_patched.sage")

set_random_seed(0xC0FFEE)
p_prime = random_prime(2^256, lbound=2^255)
q_prime = random_prime(2^256, lbound=2^255)
N = Integer(p_prime) * Integer(q_prime)
print(f"N bits = {N.nbits()}")

a = randint(2^128, 2^256)
b = randint(2^128, 2^256)
x_true = randint(0, 2^40)
y_true = randint(0, 2^40)
c = (a + x_true) * (b + y_true)
print(f"true root = ({x_true}, {y_true})")

R.<X, Y> = PolynomialRing(Zmod(N))
f = (a + X) * (b + Y) - c

roots = small_roots(f, bounds=(2^40, 2^40), m=3, d=2)
print(f"recovered roots (count={len(roots)}): {roots[:3]}")

ok = any(int(rx) == x_true and int(ry) == y_true for rx, ry in roots)
print(f"smoke test PASS = {ok}")
if not ok:
    raise SystemExit(2)
