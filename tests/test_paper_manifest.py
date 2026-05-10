"""Coverage for ``scripts/build_paper_manifest.py``.

The script's only real job is to emit a manifest where the bank's
sha256 matches what wjx_exam_assist will later recompute.  These tests
verify that round-trip and a few edge cases (missing bank, missing
static-answers) without touching the production manifest.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "build_paper_manifest.py"


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


class PaperManifestBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bank = self.root / "bank.json"
        self.bank.write_text('{"questions":[{"qid":"x","type":"single"}]}', encoding="utf-8")
        self.static = self.root / "answers.json"
        self.static.write_text('{"answers":[{"number":1,"answer":["A"]}]}', encoding="utf-8")
        self.out = self.root / "manifest.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--paper-id", "test-paper",
            "--url", "https://example.test/wjx/test.aspx#",
            "--bank", str(self.bank),
            "--static-answers", str(self.static),
            "--output", str(self.out),
            *extra,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    def test_round_trip_records_bank_sha(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        manifest = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(manifest["paper_id"], "test-paper")
        self.assertEqual(manifest["bank"]["sha256"], _sha256(self.bank))
        self.assertEqual(manifest["verified_overrides"], [])
        # Static answers block must include sha256 — this closes the
        # "same-bank only" trust boundary surfaced in Codex review.
        self.assertIsNotNone(manifest["static_answers"])
        self.assertEqual(
            manifest["static_answers"]["sha256"], _sha256(self.static)
        )

    def test_missing_bank_reports_error(self):
        self.bank.unlink()
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("bank not found", proc.stderr)

    def test_missing_static_answers_reports_error(self):
        self.static.unlink()
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("static answers not found", proc.stderr)

    def test_static_answers_optional(self):
        # When the operator deliberately omits --static-answers, the
        # manifest should still build, but with static_answers=null so
        # wjx_exam_assist refuses --static-fallback-on-risk later.
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--paper-id", "test-no-static",
            "--url", "https://example.test/wjx/none.aspx#",
            "--bank", str(self.bank),
            "--output", str(self.out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        manifest = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIsNone(manifest["static_answers"])


if __name__ == "__main__":
    unittest.main()
