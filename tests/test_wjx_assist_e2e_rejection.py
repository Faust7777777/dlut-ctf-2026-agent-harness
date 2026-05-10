"""End-to-end rejection tests for ``scripts/wjx_exam_assist.js``.

Codex review § "missing end-to-end test that 'wrong answers file is
rejected under static fallback'" — this file closes that gap.

Tests spawn the real node script with a fake URL and verify the
script exits *before* chromium.launch when the manifest's static-
answers hash doesn't match the file the operator passed via
``--answers``.  The hash gate lives early in main() so chromium is
never invoked in the rejection paths.

We verify three rejection scenarios:

  1. correct manifest + matching answers + matching bank → exit 0
     up to the chromium.launch step (we don't actually navigate, so
     a fake URL still triggers the chromium connection failure
     downstream — the test asserts the gate didn't block)
  2. correct manifest + WRONG answers file → exit 6, stderr
     contains ``static_answers_hash_mismatch``
  3. correct manifest + missing static_answers.sha256 → exit 6,
     stderr contains ``manifest_missing_static_answers_sha256``
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ASSIST = PROJECT / "scripts" / "wjx_exam_assist.js"


def sha256_of(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@unittest.skipIf(shutil.which("node") is None, "node not installed")
class WjxAssistE2ERejectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bank = self.root / "bank.json"
        self.bank.write_text('{"questions":[]}', encoding="utf-8")
        self.right_answers = self.root / "right.json"
        self.right_answers.write_text(
            '{"answers":[{"number":1,"answer":["A"]}]}', encoding="utf-8"
        )
        self.wrong_answers = self.root / "wrong.json"
        self.wrong_answers.write_text(
            '{"answers":[{"number":99,"answer":["Z"]}]}', encoding="utf-8"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _write_manifest(self, *, answers_sha256: str | None = "AUTO") -> Path:
        manifest = {
            "paper_id": "test-paper",
            "url": "http://test.invalid/",
            "bank": {
                "path": str(self.bank),
                "sha256": sha256_of(self.bank),
            },
            "static_answers": {
                "path": str(self.right_answers),
                "sha256": (
                    sha256_of(self.right_answers)
                    if answers_sha256 == "AUTO"
                    else answers_sha256
                ),
            },
            "verified_overrides": [],
        }
        if answers_sha256 is None:
            del manifest["static_answers"]["sha256"]
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def _run(self, *, manifest_path: Path, answers: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "node",
                str(ASSIST),
                "--url",
                "http://test.invalid/",
                "--paper-manifest",
                str(manifest_path),
                "--answers",
                str(answers),
                "--static-fallback-on-risk",
                "--no-submit",
                "--headless",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_wrong_answers_file_rejected_before_browser(self):
        manifest = self._write_manifest()
        proc = self._run(manifest_path=manifest, answers=self.wrong_answers)
        self.assertEqual(
            proc.returncode,
            6,
            msg=f"expected exit 6, got {proc.returncode}; stderr={proc.stderr!r}",
        )
        self.assertIn("static_answers_hash_mismatch", proc.stderr)
        # Sanity: chromium.launch logs would mention chromium / browser;
        # they must not appear when the gate fired early.
        self.assertNotIn("chromium", proc.stderr.lower())

    def test_manifest_missing_static_sha256_rejected(self):
        manifest = self._write_manifest(answers_sha256=None)
        proc = self._run(manifest_path=manifest, answers=self.right_answers)
        self.assertEqual(proc.returncode, 6)
        self.assertIn("manifest_missing_static_answers_sha256", proc.stderr)

    def test_matching_answers_does_not_trigger_rejection(self):
        manifest = self._write_manifest()
        proc = self._run(manifest_path=manifest, answers=self.right_answers)
        # When hashes match, the gate doesn't refuse; the script proceeds
        # and eventually fails when chromium tries to navigate to the
        # invalid test URL.  We assert exit code != 6 so we know the
        # rejection path was NOT the cause.
        self.assertNotEqual(
            proc.returncode,
            6,
            msg=f"static fallback gate fired despite matching hashes; stderr={proc.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
