from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
_PREFLIGHT_PATH = PROJECT / "scripts" / "runtime_preflight.py"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("runtime_preflight", _PREFLIGHT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


class RuntimePreflightTest(unittest.TestCase):
    def test_writes_capability_matrix(self):
        preflight = _load_preflight()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tools" / "bin").mkdir(parents=True)
            (root / "state").mkdir()
            fake_sage = root / "tools" / "bin" / "sage-python"
            fake_sage.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sage.chmod(0o755)

            def fake_probe(interpreter, imports, *, root):
                missing = [name for name in imports if name == "fpylll"]
                return {
                    "ok": not missing,
                    "interpreter": str(interpreter),
                    "missing": missing,
                    "stdout": "OK" if not missing else "",
                    "stderr": "",
                    "returncode": 0 if not missing else 1,
                }

            with mock.patch.object(preflight, "_probe_imports", side_effect=fake_probe):
                report = preflight.build_report(root=root)
                out = preflight.write_report(report, root=root)

            self.assertEqual(out, root / "state" / "runtime_capabilities.json")
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(saved["capabilities"]["crypto_lattice"]["available"])
            self.assertIn("fpylll", saved["capabilities"]["crypto_lattice"]["missing"])
            self.assertTrue(saved["capabilities"]["crypto_classic"]["available"])
            self.assertEqual(saved["solver_interpreter"], str(fake_sage))

    def test_reports_multivariate_coppersmith_helper_present_or_absent(self):
        preflight = _load_preflight()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tools" / "bin").mkdir(parents=True)
            (root / "tools" / "ctf_helpers" / "crypto").mkdir(parents=True)
            fake_sage = root / "tools" / "bin" / "sage-python"
            fake_sage.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_sage.chmod(0o755)
            helper = root / "tools" / "ctf_helpers" / "crypto" / "multivariate_coppersmith.sage"
            helper.write_text("small_roots = lambda *args, **kwargs: []\n", encoding="utf-8")

            with mock.patch.object(preflight, "_probe_imports", return_value={
                "ok": True,
                "interpreter": str(fake_sage),
                "missing": [],
                "stdout": "OK",
                "stderr": "",
                "returncode": 0,
            }), mock.patch.object(preflight, "_probe_sage_source", return_value={
                "ok": True,
                "interpreter": str(fake_sage),
                "stdout": "OK",
                "stderr": "",
                "returncode": 0,
            }):
                present = preflight.build_report(root=root)

            self.assertTrue(
                present["capabilities"]["crypto_multivariate_coppersmith"]["available"]
            )
            self.assertEqual(
                present["capabilities"]["crypto_multivariate_coppersmith"]["helper"],
                str(helper),
            )

            helper.unlink()
            with mock.patch.object(preflight, "_probe_imports", return_value={
                "ok": True,
                "interpreter": str(fake_sage),
                "missing": [],
                "stdout": "OK",
                "stderr": "",
                "returncode": 0,
            }):
                absent = preflight.build_report(root=root)

            self.assertFalse(
                absent["capabilities"]["crypto_multivariate_coppersmith"]["available"]
            )
            self.assertIn(
                "multivariate_coppersmith.sage",
                absent["capabilities"]["crypto_multivariate_coppersmith"]["stderr"],
            )


if __name__ == "__main__":
    unittest.main()
