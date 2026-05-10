from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.openai_expert_sidecar_dryrun as dryrun
from ctf_agents.sidecar.codex_validator import validate_codex_candidate
from ctf_agents.sidecar.openai_expert import (
    ExpertSidecarConfig,
    api_key_status,
    build_challenge_manifest,
    run_expert,
)


def _seed_challenge(root: Path, challenge_id: str = "exp-001") -> Path:
    challenge_dir = root / "artifacts" / "challenges" / challenge_id
    evidence = challenge_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (challenge_dir / "README.txt").write_text("offline challenge notes", encoding="utf-8")
    (evidence / "strings.txt").write_text("flag{expert-sidecar-test}\n", encoding="utf-8")
    return challenge_dir


class OpenAIExpertSidecarTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.challenge_dir = _seed_challenge(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_disabled_config_returns_no_action(self) -> None:
        cfg = ExpertSidecarConfig(enabled=False)
        result = run_expert(
            self.challenge_dir,
            challenge_id="exp-001",
            category="misc",
            config=cfg,
            project_root=self.root,
            mock_response={
                "notes": "should not be written",
                "candidates": [],
            },
        )

        self.assertFalse(result.ran)
        self.assertEqual(result.status, "disabled")
        self.assertFalse((self.challenge_dir / "expert_notes.md").exists())
        self.assertFalse((self.challenge_dir / "expert_candidates.json").exists())

    def test_default_config_does_not_bake_model_name(self) -> None:
        cfg = ExpertSidecarConfig()
        self.assertEqual(cfg.default_model, "")
        self.assertEqual(cfg.hard_model, "")

    def test_live_call_requires_configured_model(self) -> None:
        cfg = ExpertSidecarConfig(enabled=True)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "dummy-test-key-value"}, clear=True):
            result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
            )

        self.assertFalse(result.ran)
        self.assertEqual(result.status, "missing_model")
        self.assertEqual(result.api_key_status, "OPENAI_API_KEY=SET")

    def test_budget_and_call_limits_are_enforced_without_state_writes(self) -> None:
        cfg = ExpertSidecarConfig(
            enabled=True,
            default_model="unit-test-model",
            max_calls_total=1,
            max_calls_per_challenge=1,
            budget_usd_soft_limit=0.01,
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "dummy-test-key-value"}, clear=True):
            total_result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
                calls_total_used=1,
            )
            challenge_result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
                calls_for_challenge=1,
            )
            budget_result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
                budget_spent_usd=0.01,
            )

        self.assertFalse(total_result.ran)
        self.assertEqual(total_result.status, "call_budget_exhausted")
        self.assertFalse(challenge_result.ran)
        self.assertEqual(challenge_result.status, "challenge_call_budget_exhausted")
        self.assertFalse(budget_result.ran)
        self.assertEqual(budget_result.status, "cost_budget_exhausted")
        self.assertFalse((self.challenge_dir / "expert_notes.md").exists())
        self.assertFalse((self.challenge_dir / "expert_candidates.json").exists())

    def test_missing_api_key_returns_safe_error_without_key_value(self) -> None:
        cfg = ExpertSidecarConfig(
            enabled=True,
            default_model="unit-test-model",
            api_base_url="https://unit.test/v1",
        )
        with patch.dict(os.environ, {}, clear=True):
            result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
            )

        self.assertFalse(result.ran)
        self.assertEqual(result.status, "missing_api_key")
        self.assertEqual(result.api_key_status, "OPENAI_API_KEY=UNSET")
        self.assertNotIn("sk-", json.dumps(result.to_dict()))

    def test_api_key_status_never_returns_secret_value(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "dummy-test-key-value"}, clear=True):
            self.assertEqual(api_key_status(), "OPENAI_API_KEY=SET")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(api_key_status(), "OPENAI_API_KEY=UNSET")

    def test_manifest_rejects_challenge_dir_outside_artifact_root(self) -> None:
        outside = self.root / "tmp" / "exp-001"
        outside.mkdir(parents=True)
        cfg = ExpertSidecarConfig(enabled=True)

        with self.assertRaises(ValueError):
            build_challenge_manifest(
                outside,
                challenge_id="exp-001",
                config=cfg,
                project_root=self.root,
            )

    def test_manifest_applies_max_files_and_size_limits(self) -> None:
        for idx in range(4):
            (self.challenge_dir / f"file{idx}.txt").write_text("x", encoding="utf-8")
        cfg = ExpertSidecarConfig(enabled=True, max_input_files=3, max_attachment_mb=1)

        manifest = build_challenge_manifest(
            self.challenge_dir,
            challenge_id="exp-001",
            config=cfg,
            project_root=self.root,
        )

        self.assertLessEqual(len(manifest.files), 3)
        self.assertTrue(manifest.truncated)
        self.assertTrue(all(p.startswith("artifacts/challenges/exp-001/") for p in manifest.files))

    def test_manifest_includes_bounded_file_previews(self) -> None:
        cfg = ExpertSidecarConfig(enabled=True, max_input_files=10, max_attachment_mb=1)

        manifest = build_challenge_manifest(
            self.challenge_dir,
            challenge_id="exp-001",
            config=cfg,
            project_root=self.root,
        ).to_dict()

        entries = {entry["path"]: entry for entry in manifest["file_entries"]}
        strings = entries["artifacts/challenges/exp-001/evidence/strings.txt"]
        self.assertEqual(strings["preview_kind"], "text")
        self.assertIn("flag{expert-sidecar-test}", strings["preview"])
        self.assertLessEqual(len(strings["preview"]), cfg.max_preview_chars)

    def test_manifest_rejects_single_file_over_size_limit(self) -> None:
        big = self.challenge_dir / "big.bin"
        big.write_bytes(b"x" * 2048)
        cfg = ExpertSidecarConfig(enabled=True, max_attachment_mb=0.001)

        with self.assertRaises(ValueError):
            build_challenge_manifest(
                self.challenge_dir,
                challenge_id="exp-001",
                config=cfg,
                project_root=self.root,
            )

    def test_mock_response_writes_only_expert_outputs_and_validator_compatible_candidate(self) -> None:
        cfg = ExpertSidecarConfig(enabled=True)
        mock = {
            "notes": "Derived from evidence/strings.txt; advisory only.",
            "candidates": [
                {
                    "challenge_id": "exp-001",
                    "category": "misc",
                    "candidate": "flag{expert-sidecar-test}",
                    "confidence": "high",
                    "evidence_paths": [
                        "artifacts/challenges/exp-001/expert_notes.md",
                        "artifacts/challenges/exp-001/evidence/strings.txt",
                    ],
                    "submit_recommendation": "never_direct_submit",
                    "notes": "strings evidence supports the token",
                }
            ],
        }

        result = run_expert(
            self.challenge_dir,
            challenge_id="exp-001",
            category="misc",
            config=cfg,
            project_root=self.root,
            mock_response=mock,
        )

        self.assertTrue(result.ran)
        self.assertEqual(result.status, "ok")
        self.assertEqual(
            sorted(p.name for p in self.challenge_dir.glob("expert_*")),
            ["expert_candidates.json", "expert_notes.md"],
        )
        payload = json.loads((self.challenge_dir / "expert_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_codex_candidate(payload[0], expected_challenge_id="exp-001"), [])
        self.assertEqual(result.valid_candidates, 1)

    def test_numeric_confidence_is_normalized_for_expert_outputs(self) -> None:
        cfg = ExpertSidecarConfig(enabled=True)
        result = run_expert(
            self.challenge_dir,
            challenge_id="exp-001",
            category="misc",
            config=cfg,
            project_root=self.root,
            mock_response={
                "notes": "Numeric confidence from chat provider.",
                "candidates": [
                    {
                        "challenge_id": "exp-001",
                        "candidate": "flag{expert-sidecar-test}",
                        "confidence": 1.0,
                        "evidence_paths": [
                            "artifacts/challenges/exp-001/expert_notes.md",
                            "artifacts/challenges/exp-001/evidence/strings.txt",
                        ],
                        "submit_recommendation": "never_direct_submit",
                        "notes": "strings evidence supports the token",
                    }
                ],
            },
        )

        payload = json.loads((self.challenge_dir / "expert_candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.valid_candidates, 1)
        self.assertEqual(payload[0]["confidence"], "high")

    def test_forbidden_candidate_keys_are_written_but_reported_invalid(self) -> None:
        cfg = ExpertSidecarConfig(enabled=True)
        mock = {
            "notes": "bad response",
            "candidates": [
                {
                    "challenge_id": "exp-001",
                    "candidate": "flag{expert-sidecar-test}",
                    "confidence": "high",
                    "evidence_paths": ["artifacts/challenges/exp-001/expert_notes.md"],
                    "submit_recommendation": "never_direct_submit",
                    "notes": "tries to bypass",
                    "submit": "POST /api/game/1/challenges/1",
                }
            ],
        }

        result = run_expert(
            self.challenge_dir,
            challenge_id="exp-001",
            category="misc",
            config=cfg,
            project_root=self.root,
            mock_response=mock,
        )

        self.assertEqual(result.status, "invalid_candidates")
        self.assertEqual(result.valid_candidates, 0)
        self.assertTrue(any("forbidden keys" in err for err in result.validation_errors))

    def test_output_paths_are_fixed(self) -> None:
        cfg = ExpertSidecarConfig(enabled=True)
        result = run_expert(
            self.challenge_dir,
            challenge_id="exp-001",
            category="misc",
            config=cfg,
            project_root=self.root,
            mock_response={
                "notes_path": "../outside.md",
                "candidates_path": "../outside.json",
                "notes": "safe notes path is fixed by implementation",
                "candidates": [],
            },
        )

        self.assertEqual(result.notes_path, "artifacts/challenges/exp-001/expert_notes.md")
        self.assertEqual(result.candidates_path, "artifacts/challenges/exp-001/expert_candidates.json")
        self.assertFalse((self.root / "artifacts" / "challenges" / "outside.md").exists())

    def test_live_path_uses_responses_api_without_printing_key(self) -> None:
        cfg = ExpertSidecarConfig(
            enabled=True,
            default_model="unit-test-model",
            api_base_url="https://unit.test/v1",
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "output_text": json.dumps(
                        {
                            "notes": "API-derived advisory note",
                            "candidates": [
                                {
                                    "challenge_id": "exp-001",
                                    "candidate": "flag{expert-sidecar-test}",
                                    "confidence": "high",
                                    "evidence_paths": [
                                        "artifacts/challenges/exp-001/expert_notes.md"
                                    ],
                                    "submit_recommendation": "never_direct_submit",
                                    "notes": "evidence from bundle",
                                }
                            ],
                        }
                    )
                }

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "dummy-test-key-value"}, clear=True),
            patch("ctf_agents.sidecar.openai_expert.requests.post", return_value=FakeResponse()) as post,
        ):
            result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.api_key_status, "OPENAI_API_KEY=SET")
        self.assertNotIn("dummy-test-key-value", json.dumps(result.to_dict()))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://unit.test/v1/responses")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer dummy-test-key-value")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "unit-test-model")
        self.assertEqual(payload["reasoning"]["effort"], "high")

    def test_azure_provider_uses_configured_endpoint_and_api_key_header(self) -> None:
        cfg = ExpertSidecarConfig(
            enabled=True,
            provider="azure_openai",
            default_model="ctf-expert-deployment",
            api_base_url="https://azure-unit.test/openai/v1/",
            api_key_env="AZURE_OPENAI_API_KEY",
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"output_text": json.dumps({"notes": "no candidate", "candidates": []})}

        with (
            patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "dummy-test-key-value"}, clear=True),
            patch("ctf_agents.sidecar.openai_expert.requests.post", return_value=FakeResponse()) as post,
        ):
            result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(
            post.call_args.args[0],
            "https://azure-unit.test/openai/v1/responses",
        )
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["api-key"], "dummy-test-key-value")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "ctf-expert-deployment")

    def test_deepseek_provider_uses_chat_completions_and_api_key_header(self) -> None:
        cfg = ExpertSidecarConfig(
            enabled=True,
            provider="deepseek",
            default_model="deepseek-v4-pro",
            api_base_url="https://api.deepseek.com",
            api_key_env="DEEPSEEK_API_KEY",
        )

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "notes": "DeepSeek advisory note",
                                        "candidates": [
                                            {
                                                "challenge_id": "exp-001",
                                                "candidate": "flag{expert-sidecar-test}",
                                                "confidence": "high",
                                                "evidence_paths": [
                                                    "artifacts/challenges/exp-001/expert_notes.md"
                                                ],
                                                "submit_recommendation": "never_direct_submit",
                                                "notes": "evidence from bundle",
                                            }
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                }

        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "dummy-test-key-value"}, clear=True),
            patch("ctf_agents.sidecar.openai_expert.requests.post", return_value=FakeResponse()) as post,
        ):
            result = run_expert(
                self.challenge_dir,
                challenge_id="exp-001",
                category="misc",
                config=cfg,
                project_root=self.root,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.api_key_status, "DEEPSEEK_API_KEY=SET")
        self.assertNotIn("dummy-test-key-value", json.dumps(result.to_dict()))
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api.deepseek.com/chat/completions")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer dummy-test-key-value")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["thinking"]["type"], "enabled")

    def test_dryrun_does_not_write_real_logs_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            with patch.object(dryrun, "PROJECT", project):
                rc = dryrun.main()

            self.assertEqual(rc, 0)
            self.assertFalse((project / "logs").exists())


if __name__ == "__main__":
    unittest.main()
