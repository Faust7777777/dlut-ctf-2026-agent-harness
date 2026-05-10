from __future__ import annotations

import ast
import re
from pathlib import Path

from sage.all import Integer, RealField, PolynomialRing, Zmod, isqrt


def long_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def legendre_symbol_py(a: int, p: int) -> int:
    r = pow(a % p, (p - 1) // 2, p)
    return -1 if r == p - 1 else r


text = Path("extracted/output.txt").read_text(encoding="utf-8")
hint = Integer(re.search(r"hint = (\d+)", text).group(1))
D = Integer(re.search(r"D = (\d+)", text).group(1))
n = Integer(re.search(r"n = (\d+)", text).group(1))
c = ast.literal_eval(re.search(r"c = (\[.*\])", text, re.S).group(1))

# hint/D is a lower bound on sqrt(p) + sqrt(q).  From that, derive a
# close estimate of s = p + q, then the larger factor q0.  The error is
# below n^(1/4), so univariate Coppersmith recovers the correction.
RR = RealField(12000)
s0 = Integer(((RR(hint) / RR(D)) ** 2 - 2 * RR(n).sqrt()).round())
y0 = isqrt(s0 * s0 - 4 * n)
q0 = (s0 + y0) // 2

R = PolynomialRing(Zmod(n), "x")
x = R.gen()
roots = (x + q0).small_roots(X=2**600, beta=0.5, epsilon=0.04)

p = q = None
for root in roots:
    candidate = q0 + Integer(root)
    if candidate > 1 and n % candidate == 0:
        p = candidate
        q = n // candidate
        break

if p is None or q is None:
    raise SystemExit("factor recovery failed")
if p * q != n:
    raise SystemExit("factor verification failed")

bits = []
for ci in c:
    lp = legendre_symbol_py(int(ci), int(p))
    lq = legendre_symbol_py(int(ci), int(q))
    if lp == 1 and lq == 1:
        bits.append("1")
    elif lp == -1 and lq == -1:
        bits.append("0")
    else:
        raise SystemExit(f"unexpected mixed Legendre symbols: {lp}, {lq}")

flag = long_to_bytes(int("".join(bits), 2)).decode("utf-8")
print(f"p_bits={p.nbits()} q_bits={q.nbits()} c_len={len(c)}")
print(flag)
