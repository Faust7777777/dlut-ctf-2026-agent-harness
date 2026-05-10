"""Coverage for ``scripts/blind_solve_harness.py``.

These tests are intentionally surgical: the harness's value is its
mechanical isolation guarantees, so each test pokes at exactly one
boundary the harness must enforce.  None of these tests touch the
real public-CTF bundles — every fixture is constructed inline in a
tempdir.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))

# Spec-style import mirrors the supervisor test pattern; keeps these
# tests independent of any package layout under scripts/.
_HARNESS_PATH = PROJECT / "scripts" / "blind_solve_harness.py"
_spec = importlib.util.spec_from_file_location("blind_solve_harness", _HARNESS_PATH)
harness = importlib.util.module_from_spec(_spec)
# Register in sys.modules BEFORE executing — Python's dataclass machinery
# inspects sys.modules[cls.__module__] when wiring KW_ONLY / slots.
sys.modules[_spec.name] = harness
_spec.loader.exec_module(harness)  # type: ignore[attr-defined]


def _make_bundle(root: Path, cid: str, attachment_name: str,
                 attachment_body: bytes, expected_flag: str) -> None:
    """Mirror the layout the harness expects under
    ``artifacts/challenges/<cid>/``."""
    bdir = root / "artifacts" / "challenges" / cid
    bdir.mkdir(parents=True, exist_ok=True)
    zpath = bdir / attachment_name
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("payload.bin", attachment_body)
    (bdir / "challenge.json").write_text(
        json.dumps({"title": f"toy-{cid}", "category": "Misc",
                    "expected_flag": expected_flag,
                    "attachment_relpath": attachment_name}),
        encoding="utf-8",
    )


def _make_bundle_index(root: Path, indexes: list[tuple[str, str]]) -> None:
    """Write the two bundle_index.json files harness fingerprints by
    glob.  Content is irrelevant for these tests; only existence +
    SHA-256 matter."""
    for sub, payload in indexes:
        d = root / "artifacts" / "public-ctf-platform" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "bundle_index.json").write_text(payload, encoding="utf-8")


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Patch the module's PROJECT to our tmp root
        self._patch_project = mock.patch.object(harness, "PROJECT", self.root)
        self._patch_project.start()

    def tearDown(self):
        self._patch_project.stop()
        self.tmp.cleanup()

    # ---- 1. stage materialises only the attachment ----------------

    def test_stage_creates_workdir_with_only_attachment(self):
        _make_bundle(self.root, "100", "puzzle.zip", b"payload",
                     expected_flag="flag{stage-test}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sandbox = harness.stage("100")
        wd = sandbox.workdir
        contents = sorted(p.name for p in wd.iterdir())
        # Attachment + the empty evidence/ + extracted/ subdirs
        self.assertIn("puzzle.zip", contents)
        self.assertIn("evidence", contents)
        self.assertIn("extracted", contents)
        # The bundle's challenge.json must NOT have leaked in
        self.assertNotIn("challenge.json", contents)
        # Sandbox metadata captured the expected_flag hash, never plaintext
        expected_hash = hashlib.sha256(b"flag{stage-test}").hexdigest()
        self.assertEqual(sandbox.expected_flag_hash, expected_hash)

    # ---- 2. verify is happy when artifacts are well-formed ----------

    def _author_artifacts(self, sandbox: harness.Sandbox, *,
                          candidate: str | None,
                          evidence_files: dict[str, str] | None = None,
                          ) -> None:
        """Helper that simulates a well-behaved solver."""
        wd = sandbox.workdir
        for name in ("cc_hypothesis.md", "subagent_request.md",
                     "subagent_reply.md", "cc_final_decision.md"):
            (wd / name).write_text(f"# {name}\nbenign content\n", encoding="utf-8")
        ev_paths: list[str] = []
        for relpath, body in (evidence_files or {}).items():
            target = wd / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            ev_paths.append(relpath)
        cj = []
        if candidate is not None:
            cj.append({
                "challenge_id": "draft",
                "candidate": candidate,
                "confidence": "high",
                "evidence_paths": ev_paths,
                "submit_recommendation": "never_direct_submit",
                "notes": "ok",
            })
        (wd / "codex_candidates.json").write_text(
            json.dumps(cj), encoding="utf-8"
        )

    def test_verify_passes_for_clean_solver_output(self):
        _make_bundle(self.root, "100", "puzzle.zip", b"payload",
                     expected_flag="flag{exact-flag}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("100")
        self._author_artifacts(
            sb, candidate="flag{exact-flag}",
            evidence_files={"evidence/note.txt": "exact-flag derivation steps\n"},
        )
        audit = harness.verify(sb)
        self.assertEqual(audit["candidates_authored"], 1)
        self.assertTrue(audit["candidate_hash_check"][0]["matches_bundle_expected"])

    # ---- 3. forbidden tokens trip the audit -----------------------

    def test_verify_rejects_when_solver_mentions_challenge_json(self):
        _make_bundle(self.root, "101", "p.zip", b"x", expected_flag="flag{a}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("101")
        self._author_artifacts(sb, candidate="flag{a}")
        # Now poison cc_hypothesis with the forbidden token
        (sb.workdir / "cc_hypothesis.md").write_text(
            "I peeked at challenge.json for the flag", encoding="utf-8"
        )
        with self.assertRaises(harness.SolveError) as ctx:
            harness.verify(sb)
        self.assertIn("forbidden tokens", str(ctx.exception))

    def test_verify_rejects_when_evidence_uses_parent_traversal(self):
        _make_bundle(self.root, "102", "p.zip", b"x", expected_flag="flag{a}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("102")
        self._author_artifacts(sb, candidate="flag{a}")
        # Override codex_candidates with an evil evidence path
        (sb.workdir / "codex_candidates.json").write_text(
            json.dumps([{
                "challenge_id": "draft",
                "candidate": "flag{a}",
                "confidence": "high",
                "evidence_paths": ["../../etc/passwd"],
                "submit_recommendation": "never_direct_submit",
                "notes": "evil",
            }]), encoding="utf-8"
        )
        with self.assertRaises(harness.SolveError) as ctx:
            harness.verify(sb)
        self.assertIn("unsafe evidence path", str(ctx.exception))

    def test_verify_rejects_when_evidence_path_does_not_exist(self):
        _make_bundle(self.root, "103", "p.zip", b"x", expected_flag="flag{a}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("103")
        self._author_artifacts(sb, candidate="flag{a}",
                               evidence_files={"evidence/real.txt": "x"})
        # Edit codex_candidates.json to claim a file that doesn't exist
        cj = json.loads((sb.workdir / "codex_candidates.json").read_text())
        cj[0]["evidence_paths"] = ["evidence/missing.txt"]
        (sb.workdir / "codex_candidates.json").write_text(json.dumps(cj))
        with self.assertRaises(harness.SolveError) as ctx:
            harness.verify(sb)
        self.assertIn("not found", str(ctx.exception))

    def test_verify_rejects_when_protected_file_was_tampered(self):
        _make_bundle(self.root, "104", "p.zip", b"x", expected_flag="flag{a}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("104")
        self._author_artifacts(sb, candidate="flag{a}")
        # Simulate a misbehaving solver that writes through challenge.json
        (self.root / "artifacts" / "challenges" / "104" / "challenge.json").write_text(
            "tampered after stage", encoding="utf-8"
        )
        with self.assertRaises(harness.SolveError) as ctx:
            harness.verify(sb)
        self.assertIn("protected path tampered", str(ctx.exception))

    def test_verify_accepts_empty_codex_candidates_array(self):
        # No candidate (no_candidate path) is a legitimate outcome; the
        # five canonical files must still exist, and codex_candidates.json
        # may be `[]`.
        _make_bundle(self.root, "105", "p.zip", b"x", expected_flag="flag{q}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("105")
        self._author_artifacts(sb, candidate=None)
        audit = harness.verify(sb)
        self.assertEqual(audit["candidates_authored"], 0)

    # ---- 4. publish rewrites paths to the supervisor's expectation -

    def test_publish_rewrites_evidence_paths_to_supervisor_form(self):
        _make_bundle(self.root, "106", "p.zip", b"x", expected_flag="flag{p}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("106")
        self._author_artifacts(
            sb, candidate="flag{p}",
            evidence_files={"evidence/notes.txt": "derivation\n"},
        )
        harness.verify(sb)
        dst = harness.publish(sb)
        published_cj = json.loads((dst / "codex_candidates.json").read_text())
        self.assertEqual(len(published_cj), 1)
        evp = published_cj[0]["evidence_paths"][0]
        # Path policy: <artifacts>/challenges/<cid>/<rel>
        self.assertEqual(evp, "artifacts/challenges/106/evidence/notes.txt")
        self.assertEqual(published_cj[0]["challenge_id"], "106")
        # The actual evidence file got copied
        self.assertTrue(
            (dst / "evidence" / "notes.txt").exists(),
            "publish should mirror evidence/ tree to the supervisor sandbox"
        )

    def test_publish_keeps_extracted_subtree(self):
        _make_bundle(self.root, "107", "p.zip", b"x", expected_flag="flag{e}")
        _make_bundle_index(self.root, [("crypto-web", "{}"), ("rev-pwn", "{}")])
        sb = harness.stage("107")
        # Solver decompresses into extracted/
        (sb.workdir / "extracted" / "raw.bin").write_bytes(b"binary")
        self._author_artifacts(
            sb, candidate="flag{e}",
            evidence_files={
                "evidence/note.txt": "ok",
            },
        )
        # Re-author codex_candidates.json to also reference extracted/
        cj = json.loads((sb.workdir / "codex_candidates.json").read_text())
        cj[0]["evidence_paths"] = ["evidence/note.txt", "extracted/raw.bin"]
        (sb.workdir / "codex_candidates.json").write_text(json.dumps(cj))
        harness.verify(sb)
        dst = harness.publish(sb)
        self.assertTrue((dst / "extracted" / "raw.bin").exists())
        self.assertTrue((dst / "evidence" / "note.txt").exists())


if __name__ == "__main__":
    unittest.main()
