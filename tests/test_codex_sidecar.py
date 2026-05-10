"""Codex sidecar validator coverage.

Per ``runbooks/codex_sidecar.md``, the sidecar validator is the only
thing the supervisor trusts about Codex output.  These tests pin the
schema + sandbox boundary so future Codex prompt drift can't slip
something past in production.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctf_agents.sidecar.codex_validator import (
    ALLOWED_CONFIDENCES,
    REQUIRED_KEYS,
    FORBIDDEN_PAYLOAD_KEYS,
    check_sandbox_filesystem,
    is_safe_artifact_path,
    validate_codex_candidate,
)


def _good() -> dict:
    return {
        "challenge_id": "123",
        "candidate": "flag{not_a_real_flag_just_a_test}",
        "confidence": "medium",
        "evidence_paths": ["artifacts/challenges/123/evidence/strings.txt"],
        "submit_recommendation": "never_direct_submit",
        "notes": "found via strings on attached zip",
    }


class CodexCandidateValidatorTest(unittest.TestCase):
    def test_minimal_valid_candidate_passes(self):
        self.assertEqual(validate_codex_candidate(_good()), [])

    def test_payload_must_be_dict(self):
        for bad in [None, "string", 42, [], (1, 2)]:
            errs = validate_codex_candidate(bad)
            self.assertTrue(errs)
            self.assertIn("not an object", errs[0])

    def test_missing_required_keys_caught(self):
        for k in REQUIRED_KEYS:
            payload = _good()
            payload.pop(k)
            errs = validate_codex_candidate(payload)
            self.assertTrue(any("missing required keys" in e for e in errs), f"key={k}")

    def test_challenge_id_mismatch_caught(self):
        payload = _good()
        errs = validate_codex_candidate(payload, expected_challenge_id="999")
        self.assertTrue(any("challenge_id mismatch" in e for e in errs))

    def test_confidence_whitelist(self):
        for ok in ALLOWED_CONFIDENCES:
            payload = _good()
            payload["confidence"] = ok
            self.assertEqual(validate_codex_candidate(payload), [])
        for bad in ["maybe", "high+", "", None, "HIGH", 1]:
            payload = _good()
            payload["confidence"] = bad
            errs = validate_codex_candidate(payload)
            self.assertTrue(any("confidence" in e for e in errs), f"bad={bad!r}")

    def test_submit_recommendation_must_be_never_direct(self):
        for bad in ["auto_submit", "submit", "yes", "now", "", None]:
            payload = _good()
            payload["submit_recommendation"] = bad
            errs = validate_codex_candidate(payload)
            self.assertTrue(any("submit_recommendation" in e for e in errs))

    def test_candidate_must_be_non_empty_string(self):
        for bad in [None, "", "   ", 42, [], {}]:
            payload = _good()
            payload["candidate"] = bad
            errs = validate_codex_candidate(payload)
            self.assertTrue(any("candidate" in e for e in errs), f"bad={bad!r}")

    def test_evidence_paths_must_be_non_empty_list(self):
        for bad in [None, "single string", [], {}]:
            payload = _good()
            payload["evidence_paths"] = bad
            errs = validate_codex_candidate(payload)
            self.assertTrue(any("evidence_paths" in e for e in errs), f"bad={bad!r}")

    def test_evidence_path_absolute_rejected(self):
        payload = _good()
        payload["evidence_paths"] = ["/etc/passwd"]
        errs = validate_codex_candidate(payload)
        self.assertTrue(any("absolute" in e or "blocked" in e for e in errs))

    def test_evidence_path_traversal_rejected(self):
        payload = _good()
        payload["evidence_paths"] = ["artifacts/challenges/../../.secrets/cookies.json"]
        errs = validate_codex_candidate(payload)
        self.assertTrue(any(".." in e or "blocked" in e or "traversal" in e for e in errs))

    def test_evidence_path_secrets_dir_blocked(self):
        for bad in [
            ".secrets/gzctf_cookies.json",
            "artifacts/.secrets/leaked.json",
            "artifacts/challenges/.env",
        ]:
            payload = _good()
            payload["evidence_paths"] = [bad]
            errs = validate_codex_candidate(payload)
            self.assertTrue(errs, f"path should be rejected: {bad}")

    def test_evidence_path_state_files_blocked(self):
        for bad in [
            "state/submission_state.json",
            "artifacts/state/ai_contest_state.json",
        ]:
            payload = _good()
            payload["evidence_paths"] = [bad]
            errs = validate_codex_candidate(payload)
            self.assertTrue(errs, f"path should be rejected: {bad}")

    def test_evidence_path_must_live_under_artifacts(self):
        payload = _good()
        payload["evidence_paths"] = ["scripts/exfiltrate.py"]
        errs = validate_codex_candidate(payload)
        self.assertTrue(any("artifacts/challenges/" in e for e in errs))

    def test_evidence_path_loose_substring_artifacts_rejected(self):
        # Codex review §3: "tmp/artifacts/x" used to slip through
        # because the old check used "/artifacts/" substring matching.
        # Strict prefix rule rejects it.
        payload = _good()
        payload["evidence_paths"] = ["tmp/artifacts/x"]
        errs = validate_codex_candidate(payload)
        self.assertTrue(
            any("must start with artifacts/challenges/" in e for e in errs),
            f"loose substring path was not rejected; errors={errs}",
        )

    def test_evidence_path_sibling_challenge_id_rejected_when_pinned(self):
        # Codex review §3: when expected_challenge_id is pinned (the
        # supervisor always pins it), evidence paths must not point to
        # a sibling challenge's artifact tree.
        payload = _good()
        payload["challenge_id"] = "123"
        payload["evidence_paths"] = ["artifacts/challenges/999/leak.txt"]
        errs = validate_codex_candidate(payload, expected_challenge_id="123")
        self.assertTrue(
            any("artifacts/challenges/123/" in e for e in errs),
            f"sibling-id path slipped through; errors={errs}",
        )

    def test_evidence_path_pinned_id_inferred_from_payload(self):
        # When expected_challenge_id is omitted, the validator falls
        # back to the payload's own challenge_id for path pinning.
        # Payload says id=123 but path references 999 → reject.
        payload = _good()
        payload["challenge_id"] = "123"
        payload["evidence_paths"] = ["artifacts/challenges/999/leak.txt"]
        errs = validate_codex_candidate(payload)  # no expected_challenge_id arg
        self.assertTrue(
            any("artifacts/challenges/123/" in e for e in errs),
            f"payload-id pinning didn't fire; errors={errs}",
        )

    def test_forbidden_payload_keys_rejected(self):
        for k in FORBIDDEN_PAYLOAD_KEYS:
            payload = _good()
            payload[k] = "anything"
            errs = validate_codex_candidate(payload)
            self.assertTrue(
                any("forbidden keys" in e for e in errs),
                f"forbidden key not caught: {k}"
            )

    def test_notes_must_be_string_or_none(self):
        payload = _good()
        payload["notes"] = None
        # None is permitted (notes are optional clarification)
        self.assertEqual(validate_codex_candidate(payload), [])
        payload["notes"] = {"oops": "not a string"}
        errs = validate_codex_candidate(payload)
        self.assertTrue(any("notes" in e for e in errs))


class IsSafeArtifactPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "artifacts").mkdir()
        (self.root / "artifacts" / "challenges").mkdir()
        (self.root / ".secrets").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_inside_artifacts_ok(self):
        self.assertTrue(is_safe_artifact_path("artifacts/challenges/123/foo.txt", self.root))
        self.assertTrue(is_safe_artifact_path(self.root / "artifacts" / "x.bin", self.root))

    def test_outside_artifacts_rejected(self):
        self.assertFalse(is_safe_artifact_path(".secrets/cookies.json", self.root))
        self.assertFalse(is_safe_artifact_path("scripts/x.py", self.root))
        self.assertFalse(is_safe_artifact_path("/tmp/elsewhere", self.root))
        self.assertFalse(is_safe_artifact_path("../../etc/passwd", self.root))


class SandboxFilesystemCheckTest(unittest.TestCase):
    def test_returns_layout_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "artifacts").mkdir()
            info = check_sandbox_filesystem(root)
            self.assertTrue(info["artifacts_dir_present"])
            self.assertFalse(info["secrets_dir_present"])  # not seeded
            self.assertIn(".env", info["out_of_bounds"])


if __name__ == "__main__":
    unittest.main()
