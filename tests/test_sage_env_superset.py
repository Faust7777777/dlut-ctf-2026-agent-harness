from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class SageEnvSupersetTest(unittest.TestCase):
    def test_sage_python_has_full_solver_stack(self):
        out = subprocess.check_output(
            [
                str(PROJECT / "tools" / "bin" / "sage-python"),
                "-c",
                (
                    "import sage.all, fpylll, mpmath, sympy, gmpy2; "
                    "from Crypto.Util.number import getPrime; "
                    "import z3; "
                    "from pwn import context; "
                    "import requests, bs4; "
                    "print('OK')"
                ),
            ],
            cwd=PROJECT,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        self.assertIn("OK", out)


if __name__ == "__main__":
    unittest.main()

