from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / "tools" / "ctf_helpers" / "crypto" / "multivariate_coppersmith.sage"


class MultivariateCoppersmithHelperTest(unittest.TestCase):
    def test_sage_loads_helper_and_recovers_deterministic_bivariate_root(self):
        source = textwrap.dedent(
            f"""
            load({str(HELPER)!r})

            p = 11412806115674165783
            q = 11813486495389702937
            N = p * q
            a = 0x123456789abcdef
            b = 0xfedcba987654321
            x_true = 12345
            y_true = 67890
            c = (a + x_true) * (b + y_true)

            R.<X, Y> = PolynomialRing(Zmod(N))
            f = (a + X) * (b + Y) - c
            roots = small_roots(f, bounds=(2^17, 2^17), m=2, d=2)
            recovered = sorted((int(rx), int(ry)) for rx, ry in roots)
            print(recovered)
            if (x_true, y_true) not in recovered:
                raise SystemExit(2)
            """
        )

        out = subprocess.check_output(
            [str(PROJECT / "tools" / "bin" / "sage"), "-c", source],
            cwd=PROJECT,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=45,
        )

        self.assertIn("(12345, 67890)", out)


if __name__ == "__main__":
    unittest.main()
