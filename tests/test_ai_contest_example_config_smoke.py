"""Offline smoke coverage for the AI contest example config.

The example config is the operator-facing default.  This test keeps it
aligned with the deterministic supervisor defaults and the current
route-control state machine without touching the network or platform.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.common.yaml_compat import safe_load_file  # noqa: E402
from ctf_agents.contest.route_control import (  # noqa: E402
    ExpertReviewStatus,
    FamilyLedgerEntry,
    FailureType,
    PersistentLaneStatus,
    ProgressType,
    PublicSearchStatus,
    RouteDecision,
    RoutePhase,
    RouteState,
    can_emit_no_candidate,
)
from ctf_agents.submit.decisions import Decision  # noqa: E402
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard  # noqa: E402

_SUP_PATH = PROJECT / "scripts" / "ai_contest_supervisor.py"
_spec = importlib.util.spec_from_file_location("ai_contest_supervisor", _SUP_PATH)
sup_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(sup_mod)  # type: ignore[attr-defined]


CONFIG_PATH = PROJECT / "configs" / "ai_contest.example.yaml"
ROUTE_DOCS = (
    PROJECT / "docs" / "harness_route_control.md",
    PROJECT / "docs" / "harness_route_control_handoff.md",
)
AI_IDENTITY_DOCS = (
    PROJECT / "runbooks" / "ai_identity.md",
    PROJECT / "runbooks" / "contest_day_ai_identity.md",
)
SOLVE_FIRST_DOCS = (
    PROJECT / "docs" / "solve_first_loop_policy.md",
    PROJECT / "docs" / "loop_prompt_solve_first.md",
)


class AIContestExampleConfigSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = safe_load_file(CONFIG_PATH)

    def test_example_config_parses_with_required_sections(self) -> None:
        self.assertIsInstance(self.cfg, dict)
        self.assertEqual(
            set(self.cfg),
            {
                "project",
                "gzctf",
                "scope",
                "submit",
                "agent",
                "paths",
                "feishu",
                "codex_sidecar",
                "expert_sidecar",
            },
        )
        self.assertEqual(self.cfg["project"]["timezone"], "Asia/Shanghai")
        self.assertEqual(self.cfg["gzctf"]["auth_mode"], "auto")
        self.assertGreater(int(self.cfg["gzctf"]["game_id"]), 0)
        self.assertEqual(float(self.cfg["gzctf"]["poll_timeout_s"]), 90.0)
        self.assertEqual(float(self.cfg["gzctf"]["poll_interval_s"]), 3.0)

    def test_submit_and_agent_defaults_match_supervisor_safe_path(self) -> None:
        submit = self.cfg["submit"]
        agent = self.cfg["agent"]

        self.assertTrue(submit["auto_submit"])
        # AI identity: every category auto-submits; thresholds 0.0; no
        # forced HUMAN_REVIEW for pwn/reverse.
        self.assertEqual(
            submit["auto_submit_categories"],
            ["misc", "forensics", "crypto", "web", "reverse", "pwn"],
        )
        self.assertEqual(float(submit["min_conf_auto_submit"]), 0.0)
        self.assertEqual(float(submit["min_conf_human_review"]), 0.0)
        self.assertEqual(int(submit["max_wrong_per_challenge"]), 1)
        self.assertEqual(submit["state_path"], "state/submission_state.json")
        self.assertEqual(submit["kill_switch_file"], ".auto_submit_off")
        self.assertEqual(int(submit["force_submit_min_reason_length"]), 10)
        self.assertFalse(submit["pwn_reverse_force_human_review"])

        flag_re = re.compile(submit["flag_regex"])
        self.assertIsNotNone(flag_re.search("flag{abcd}"))
        self.assertIsNotNone(flag_re.search("DLUTCTF{abcd}"))
        self.assertIsNone(flag_re.search("flag{x}"))

        self.assertEqual(
            agent["enabled_categories"],
            ["misc", "forensics", "crypto", "web", "reverse", "pwn"],
        )
        # Built-in agent registry covers misc/forensics; the other
        # categories enter the route-control NO_CANDIDATE flow when no
        # Codex sidecar candidate is produced.
        self.assertTrue({"misc", "forensics"}.issubset(sup_mod.DEFAULT_AGENT_REGISTRY))
        self.assertEqual(int(agent["challenge_loop_interval_s"]), 30)
        self.assertEqual(int(agent["challenge_solve_timeout_s"]), 600)
        self.assertEqual(int(agent["global_run_timeout_s"]), 14400)
        self.assertEqual(int(agent["heartbeat_interval_s"]), 60)

    def test_paths_and_sidecars_default_to_offline_safe_values(self) -> None:
        paths = self.cfg["paths"]
        self.assertEqual(paths["state_dir"], "state")
        self.assertEqual(paths["artifacts_dir"], "artifacts")
        self.assertEqual(paths["logs_dir"], "logs")
        self.assertEqual(paths["locks_dir"], "state/locks")

        codex = self.cfg["codex_sidecar"]
        self.assertFalse(codex["enabled"])
        self.assertTrue(codex["allow_patch"])
        self.assertFalse(codex["allow_submit"])
        self.assertFalse(codex["allow_secret_read"])
        self.assertEqual(codex["artifact_root"], "artifacts/challenges")

        expert = self.cfg["expert_sidecar"]
        self.assertFalse(expert["enabled"])
        self.assertEqual(expert["provider"], "deepseek")
        self.assertEqual(expert["default_model"], "deepseek-v4-pro")
        self.assertEqual(expert["hard_model"], "deepseek-v4-pro")
        self.assertEqual(expert["api_base_url"], "https://api.deepseek.com")
        self.assertEqual(expert["api_key_env"], "DEEPSEEK_API_KEY")
        self.assertEqual(expert["allowed_categories"], ["misc", "forensics", "crypto", "reverse", "web"])
        self.assertEqual(expert["disallowed_paths"], [".env", ".secrets", "state", "logs"])

        config_text = CONFIG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("refuse to honour", config_text)

    def test_contest_profile_auto_submits_every_category(self) -> None:
        """Under the AI-identity profile a valid candidate from any of
        the six categories must reach AUTO_SUBMIT.  HUMAN_REVIEW is not
        a normal-flow branch on this profile."""
        submit_cfg = dict(self.cfg["submit"])
        # Tighten timing so per-category iteration in this unit test
        # doesn't stall behind real submit windows.  Hard gates remain.
        submit_cfg["min_seconds_between_submits_global"] = 0
        submit_cfg["min_seconds_between_submits_per_challenge"] = 0

        for category in ("misc", "forensics", "crypto", "web", "reverse", "pwn"):
            with self.subTest(category=category):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    cfg = dict(submit_cfg)
                    cfg["state_path"] = str(root / "submission_state.json")
                    guard = FlagGuard(project_root=root, submit_cfg=cfg)
                    cand = FlagCandidate(
                        challenge_id=f"id-{category}",
                        flag=f"flag{{ai-identity-{category}-strong}}",
                        category=category,
                        evidence_count=1,
                        extraction_confidence=0.50,
                        agent_votes=[],
                    )
                    decision = guard.decide(cand)
                    self.assertIs(
                        decision.action,
                        Decision.AUTO_SUBMIT,
                        f"category={category!r} reached "
                        f"{decision.action.value} ({decision.reason}); "
                        f"AI-identity profile must AUTO_SUBMIT every "
                        f"valid candidate",
                    )

    def test_contest_profile_low_confidence_still_auto_submits(self) -> None:
        """A near-zero confidence valid candidate is still AUTO_SUBMIT
        under AI identity (thresholds are 0.0).  This pins that the
        score-band HUMAN_REVIEW branch is unreachable on this profile."""
        submit_cfg = dict(self.cfg["submit"])
        submit_cfg["min_seconds_between_submits_global"] = 0
        submit_cfg["min_seconds_between_submits_per_challenge"] = 0
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            submit_cfg["state_path"] = str(root / "submission_state.json")
            guard = FlagGuard(project_root=root, submit_cfg=submit_cfg)
            # No evidence, no votes — confidence_score floor for a
            # well-formed flag is 0.35 in the FlagGuard, well below the
            # legacy 0.92 threshold but >= the 0.0 AI-identity threshold.
            cand = FlagCandidate(
                challenge_id="lowconf-pwn",
                flag="flag{low-confidence-but-valid-format}",
                category="pwn",
                evidence_count=0,
                extraction_confidence=0.0,
                agent_votes=[],
            )
            decision = guard.decide(cand)
            self.assertIs(decision.action, Decision.AUTO_SUBMIT)

    def test_contest_profile_hard_gates_still_block(self) -> None:
        """Verify the AI-identity profile still blocks via the hard
        gates: format-bad flags REJECT, frozen challenges HUMAN_REVIEW,
        kill-switch downgrades to HUMAN_REVIEW."""
        submit_cfg = dict(self.cfg["submit"])
        submit_cfg["min_seconds_between_submits_global"] = 0
        submit_cfg["min_seconds_between_submits_per_challenge"] = 0
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            submit_cfg["state_path"] = str(root / "submission_state.json")
            guard = FlagGuard(project_root=root, submit_cfg=submit_cfg)

            bad_format = guard.decide(FlagCandidate(
                challenge_id="fmt-1", flag="not-a-flag", category="misc",
            ))
            self.assertIs(bad_format.action, Decision.REJECT)

            frozen_cand = FlagCandidate(
                challenge_id="frz-1",
                flag="flag{strong-frozen-test}",
                category="web",
                evidence_count=2,
                extraction_confidence=0.80,
                agent_votes=["flag{strong-frozen-test}"],
            )
            guard.state_store.force_freeze("frz-1", reason="test")
            frozen_decision = guard.decide(frozen_cand)
            self.assertIs(frozen_decision.action, Decision.HUMAN_REVIEW)

            ks_path = root / submit_cfg["kill_switch_file"]
            ks_path.write_text("on", encoding="utf-8")
            kill_decision = guard.decide(FlagCandidate(
                challenge_id="ks-1",
                flag="flag{strong-kill-switch-test}",
                category="reverse",
                evidence_count=2,
                extraction_confidence=0.80,
                agent_votes=["flag{strong-kill-switch-test}"],
            ))
            self.assertIs(kill_decision.action, Decision.HUMAN_REVIEW)

    def test_run_all_tests_keeps_example_config_smoke_on_the_default_path(self) -> None:
        runner_text = (PROJECT / "scripts" / "run_all_tests.sh").read_text(encoding="utf-8")
        self.assertIn(
            'run_step "unit: ai_contest_example_config_smoke" python -m unittest tests.test_ai_contest_example_config_smoke',
            runner_text,
        )

    def test_route_control_defaults_match_supervisor_serialization(self) -> None:
        self.assertEqual(
            sup_mod._default_route_control_state(),
            RouteState.new(current_family="misc.initial").to_dict(),
        )
        default_route = sup_mod._default_route_control_state()
        self.assertEqual(default_route["current_family"], "misc.initial")
        self.assertEqual(default_route["route_decision"], RouteDecision.CONTINUE_ROUTE.value)
        self.assertEqual(default_route["route_phase"], RoutePhase.ACTIVE.value)
        self.assertEqual(default_route["public_search_status"], PublicSearchStatus.NOT_REQUIRED.value)
        self.assertEqual(default_route["expert_review_status"], ExpertReviewStatus.NOT_REQUIRED.value)
        self.assertEqual(default_route["persistent_lane_status"], PersistentLaneStatus.NOT_STARTED.value)
        self.assertFalse(default_route["candidate_queue_empty"])
        self.assertFalse(default_route["local_baseline_done"])
        self.assertFalse(default_route["short_codex_done"])
        self.assertFalse(default_route["family_switch_done"])

        self.assertEqual(
            {progress.value for progress in ProgressType},
            {
                "challenge_progress",
                "capability_progress",
                "negative_progress",
                "no_progress",
                "platform_progress",
                "documentation_progress",
            },
        )
        self.assertEqual(
            {failure.value for failure in FailureType},
            {
                "parameteric_failure",
                "structural_failure",
                "tool_failure",
                "representation_mismatch",
                "evidence_insufficient",
                "wrong_target",
                "helper_bound_limit",
            },
        )
        self.assertEqual(
            {decision.value for decision in RouteDecision},
            {
                "continue_route",
                "cut_route",
                "switch_family",
                "spawn_public_search",
                "spawn_expert_review",
                "spawn_persistent_lane",
                "block_no_candidate",
                "allow_no_candidate",
            },
        )
        self.assertEqual({phase.value for phase in RoutePhase}, {"active", "cut", "exhausted"})
        self.assertEqual(
            {status.value for status in PublicSearchStatus},
            {"not_required", "required", "running", "complete", "blocked_by_rules"},
        )
        self.assertEqual(
            {status.value for status in ExpertReviewStatus},
            {"not_required", "required", "running", "complete"},
        )
        self.assertEqual(
            {status.value for status in PersistentLaneStatus},
            {"not_started", "active", "suspended", "complete", "stale", "stopped"},
        )

    def test_no_candidate_requires_exhausted_machine_state(self) -> None:
        route = RouteState.new(current_family="crypto.unknown")
        self.assertFalse(can_emit_no_candidate(route))

        route.route_phase = RoutePhase.EXHAUSTED
        route.public_search_status = PublicSearchStatus.COMPLETE
        route.expert_review_status = ExpertReviewStatus.COMPLETE
        route.persistent_lane_status = PersistentLaneStatus.COMPLETE
        route.candidate_queue_empty = True
        route.local_baseline_done = True
        route.short_codex_done = True
        route.family_switch_done = True
        route.failure_type = FailureType.EVIDENCE_INSUFFICIENT
        route.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=1,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT,
                failure_signals=["local baseline and short codex exhausted"],
                exhaustion_reason="no candidate after required route gates",
            )
        )
        self.assertTrue(can_emit_no_candidate(route))

        route.persistent_lane.no_candidate_blockers = ["open_math_boundary"]
        self.assertFalse(can_emit_no_candidate(route))

    def test_ai_identity_docs_and_loop_prompts_share_the_route_contract(self) -> None:
        doc_expectations = {
            AI_IDENTITY_DOCS[0]: (
                "Route control is persisted under each challenge in supervisor state; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate.",
                "Guard is the only submit gate.",
                "Helper smoke success is `capability_progress`, not `challenge_progress`.",
            ),
            AI_IDENTITY_DOCS[1]: (
                "Route-control state lives under each challenge in `state/ai_contest_state.json`; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate.",
                "The submit chain remains `supervisor -> validator -> FlagGuard -> adapter`",
                "helper smoke success only counts as `capability_progress`.",
            ),
            SOLVE_FIRST_DOCS[0]: (
                "Route control is persisted under each challenge in `state/ai_contest_state.json`; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate.",
                "The submit chain stays `supervisor -> validator -> FlagGuard -> adapter`.",
                "Add a toy/smoke test proving the helper works, and record that as `capability_progress`, not `challenge_progress`.",
            ),
            SOLVE_FIRST_DOCS[1]: (
                "Route-control state is persisted per challenge; `current_family`, `tried_families`, and `failure_type` drive `public_search`, `expert_review`, `persistent_lane`, and the `NO_CANDIDATE` gate",
                "helper smoke success is `capability_progress`, not `challenge_progress`.",
                "flag submissions remain controlled by supervisor, validator, guard, and adapter.",
            ),
        }
        for path, expected_lines in doc_expectations.items():
            text = path.read_text(encoding="utf-8")
            for line in expected_lines:
                self.assertIn(line, text, f"{line!r} missing from {path.name}")

    def test_route_control_docs_track_current_enums_and_model_defaults(self) -> None:
        expected_lines = {
            "progress_type: challenge_progress | capability_progress | negative_progress | no_progress | platform_progress | documentation_progress",
            "failure_type: parameteric_failure | structural_failure | tool_failure | representation_mismatch | evidence_insufficient | wrong_target | helper_bound_limit",
            "route_decision: continue_route | cut_route | switch_family | spawn_public_search | spawn_expert_review | spawn_persistent_lane | block_no_candidate | allow_no_candidate",
            "route_phase: active | cut | exhausted",
            "public_search_status: not_required | required | running | complete | blocked_by_rules",
            "expert_review_status: not_required | required | running | complete",
            "persistent_lane_status: not_started | active | suspended | complete | stale | stopped",
            "route_control_action_state",
            "public_search_result.json",
            "public_search_ledger.json",
            "expert_review_result.json",
            "persistent_lane_update.json",
            "persistent_lane_stop_report.json",
            "Packet emission is tracked by supervisor state.",
            "The supervisor writes request packets and advances `route_control_action_state` when it emits a route action.",
            "Result consumption is a separate contract from packet emission.",
            "`codex_sidecar.allow_submit` and `codex_sidecar.allow_secret_read` are audit defaults, not route-control enforcement knobs.",
        }
        for path in ROUTE_DOCS:
            text = path.read_text(encoding="utf-8")
            for line in expected_lines:
                self.assertIn(line, text, f"{line!r} missing from {path.name}")
            self.assertNotIn("gpt-5.4-pro", text)
            self.assertNotIn("codex_sidecar allow_submit gate", text)
            self.assertNotIn("codex_sidecar allow_secret_read gate", text)
            self.assertNotIn("allow_submit is an enforced knob", text)
            self.assertNotIn("allow_secret_read is an enforced knob", text)


if __name__ == "__main__":
    unittest.main()
