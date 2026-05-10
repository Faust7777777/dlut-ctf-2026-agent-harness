"""Route-control state machine coverage.

The route-control layer is deliberately pure: no platform I/O, no
submit decisions, and no Codex path validation.  These tests pin the
machine-readable state that the supervisor persists under each
challenge.
"""
from __future__ import annotations

import unittest

from ctf_agents.contest.route_control import (
    FamilyLedgerEntry,
    FailureType,
    PersistentLaneStatus,
    ProgressType,
    PublicSearchStatus,
    RouteDecision,
    RoutePhase,
    RouteState,
    classify_failure,
    classify_progress,
    evaluate_route,
)


class RouteControlTest(unittest.TestCase):
    def test_route_state_serializes_with_sane_defaults(self):
        state = RouteState.new(
            current_family="crypto.lattice.multivariate_coppersmith",
            route_budget_remaining=3,
        )

        payload = state.to_dict()

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(
            payload["current_family"],
            "crypto.lattice.multivariate_coppersmith",
        )
        self.assertEqual(payload["tried_families"], [])
        self.assertIsNone(payload["failure_type"])
        self.assertEqual(payload["failure_signals"], [])
        self.assertEqual(payload["evidence_delta_score"], 0)
        self.assertEqual(payload["same_family_no_delta_count"], 0)
        self.assertEqual(payload["trivial_root_count"], 0)
        self.assertIsNone(payload["next_family"])
        self.assertEqual(payload["route_decision"], "continue_route")
        self.assertEqual(payload["route_phase"], "active")
        self.assertEqual(payload["route_cycle"], 0)
        self.assertEqual(payload["route_budget_remaining"], 3)
        self.assertEqual(payload["public_search_status"], "not_required")
        self.assertEqual(payload["expert_review_status"], "not_required")
        self.assertEqual(payload["persistent_lane_status"], "not_started")
        self.assertEqual(payload["pending_actions"], [])
        self.assertEqual(payload["no_candidate_blockers"], [])

        self.assertEqual(RouteState.from_dict(payload).to_dict(), payload)

    def test_family_entry_has_auditable_route_contract_defaults(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        entry = state.ensure_family_entry()
        payload = entry.to_dict()

        self.assertTrue(payload["hypothesis"])
        self.assertTrue(payload["continue_if"])
        self.assertTrue(payload["cut_if"])
        self.assertEqual(
            payload["expected_evidence_delta"],
            "new candidate evidence or qualified negative evidence",
        )
        self.assertIsNone(payload["exhaustion_reason"])
        self.assertEqual(
            FamilyLedgerEntry(
                route_id="route_999",
                family="crypto.lattice.small_roots",
            ).to_dict()["continue_if"],
            "",
        )

    def test_ensure_family_entry_applies_route_start_contract_explicitly(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        entry = state.ensure_family_entry()

        self.assertEqual(entry.hypothesis, "route family may fit available artifacts")
        self.assertEqual(entry.continue_if, "challenge evidence delta is observed")
        self.assertEqual(
            entry.cut_if,
            "route assumptions fail, helper bounds are exceeded, or no evidence delta repeats",
        )
        self.assertEqual(
            entry.expected_evidence_delta,
            "new candidate evidence or qualified negative evidence",
        )

    def test_legacy_route_state_loads_and_round_trips_with_schema_version(self):
        legacy_payload = {
            "current_family": "misc.initial",
            "tried_families": [],
            "failure_type": None,
            "failure_signals": [],
            "route_decision": "continue_route",
            "route_phase": "active",
            "public_search_status": "not_required",
            "expert_review_status": "not_required",
            "persistent_lane_status": "not_started",
            "no_candidate_blockers": [],
        }

        migrated = RouteState.from_dict(legacy_payload).to_dict()

        self.assertEqual(migrated["schema_version"], 1)
        self.assertEqual(migrated["current_family"], "misc.initial")
        self.assertEqual(migrated["pending_actions"], [])
        self.assertEqual(RouteState.from_dict(migrated).to_dict(), migrated)

    def test_capability_progress_does_not_reset_stall_counter(self):
        state = RouteState.new(current_family="misc.initial")
        state.same_family_no_delta_count = 1

        updated = evaluate_route(
            state,
            progress=ProgressType.CAPABILITY_PROGRESS,
            failure_signals=["helper smoke test passed"],
        )

        self.assertEqual(updated.same_family_no_delta_count, 2)
        self.assertEqual(updated.route_decision, RouteDecision.SPAWN_PUBLIC_SEARCH)
        self.assertIn("same_family_stalled_twice", updated.no_candidate_blockers)

    def test_pending_actions_retain_cut_and_search_when_decision_advances(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        updated = evaluate_route(
            state,
            progress=ProgressType.NO_PROGRESS,
            failure_type=FailureType.HELPER_BOUND_LIMIT,
            failure_signals=["bound check negative", "helper returned only trivial roots"],
        )

        self.assertEqual(updated.route_decision, RouteDecision.SPAWN_PUBLIC_SEARCH)
        self.assertIn(RouteDecision.CUT_ROUTE, updated.pending_actions)
        self.assertIn(RouteDecision.SPAWN_PUBLIC_SEARCH, updated.pending_actions)
        self.assertIn(RouteDecision.SPAWN_PERSISTENT_LANE, updated.pending_actions)

        rerun = evaluate_route(updated, progress=ProgressType.NO_PROGRESS)
        self.assertEqual(
            rerun.pending_actions.count(RouteDecision.SPAWN_PUBLIC_SEARCH),
            1,
        )

    def test_cut_route_uses_next_family_for_switch_after_search_is_done(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")
        state.next_family = "crypto.algebraic.elimination"
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.persistent_lane_status = PersistentLaneStatus.ACTIVE

        updated = evaluate_route(
            state,
            progress=ProgressType.NO_PROGRESS,
            failure_type=FailureType.HELPER_BOUND_LIMIT,
            failure_signals=["real instance outside helper math coverage"],
        )

        self.assertEqual(updated.next_family, "crypto.algebraic.elimination")
        self.assertEqual(updated.route_decision, RouteDecision.SWITCH_FAMILY)
        self.assertIn(RouteDecision.SWITCH_FAMILY, updated.pending_actions)
        self.assertNotIn(RouteDecision.SPAWN_PUBLIC_SEARCH, updated.pending_actions)

    def test_no_candidate_path_spawns_persistent_lane_before_blocking(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=3,
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.NOT_STARTED
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.SPAWN_PERSISTENT_LANE)
        self.assertIn(RouteDecision.SPAWN_PERSISTENT_LANE, updated.pending_actions)
        self.assertIn("persistent_lane_required", updated.no_candidate_blockers)

    def test_challenge_progress_resets_stall_counter(self):
        state = RouteState.new(current_family="web.source_review")
        state.same_family_no_delta_count = 2

        updated = evaluate_route(
            state,
            progress=ProgressType.CHALLENGE_PROGRESS,
            evidence_delta_score=3,
            failure_signals=["recovered endpoint and token format"],
        )

        self.assertEqual(updated.same_family_no_delta_count, 0)
        self.assertEqual(updated.evidence_delta_score, 3)
        self.assertEqual(updated.route_decision, RouteDecision.CONTINUE_ROUTE)

    def test_helper_bound_limit_cuts_route_and_requires_search(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        updated = evaluate_route(
            state,
            progress=ProgressType.NO_PROGRESS,
            failure_type=FailureType.HELPER_BOUND_LIMIT,
            failure_signals=["bound check negative", "helper returned only trivial roots"],
        )

        self.assertEqual(updated.failure_type, FailureType.HELPER_BOUND_LIMIT)
        self.assertEqual(updated.route_decision, RouteDecision.SPAWN_PUBLIC_SEARCH)
        self.assertEqual(updated.route_phase, RoutePhase.CUT)
        self.assertEqual(updated.public_search_status, PublicSearchStatus.REQUIRED)
        self.assertIn("public_search_required", updated.no_candidate_blockers)
        self.assertIn("helper_bound_limit", updated.no_candidate_blockers)
        self.assertEqual(updated.tried_families[-1].failure_type, FailureType.HELPER_BOUND_LIMIT)
        self.assertEqual(updated.tried_families[-1].cut_reason, "helper_bound_limit")

    def test_negative_bound_certificate_classifies_as_helper_bound_limit(self):
        failure = classify_failure(["negative bound certificate from helper"])

        self.assertEqual(failure, FailureType.HELPER_BOUND_LIMIT)

    def test_repeated_trivial_roots_cut_route(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        first = evaluate_route(
            state,
            progress=ProgressType.NO_PROGRESS,
            failure_signals=["trivial_root"],
        )
        second = evaluate_route(
            first,
            progress=ProgressType.NO_PROGRESS,
            failure_signals=["trivial_root"],
        )

        self.assertEqual(second.trivial_root_count, 2)
        self.assertEqual(second.route_phase, RoutePhase.CUT)
        self.assertEqual(second.route_decision, RouteDecision.SPAWN_PUBLIC_SEARCH)
        self.assertIn("trivial_root_repetition", second.no_candidate_blockers)

    def test_structural_failure_cuts_route_and_requires_public_search(self):
        state = RouteState.new(current_family="reverse.vm")

        updated = evaluate_route(
            state,
            progress=ProgressType.NEGATIVE_PROGRESS,
            failure_type=FailureType.STRUCTURAL_FAILURE,
            failure_signals=["bytecode shape does not match VM hypothesis"],
        )

        self.assertEqual(updated.route_phase, RoutePhase.CUT)
        self.assertEqual(updated.route_decision, RouteDecision.CUT_ROUTE)
        self.assertEqual(updated.public_search_status, PublicSearchStatus.REQUIRED)
        self.assertIn("public_search_required", updated.no_candidate_blockers)
        self.assertIn("route_cut", updated.no_candidate_blockers)

    def test_structural_failure_with_negative_bound_cuts_route_immediately(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        updated = evaluate_route(
            state,
            progress=ProgressType.NEGATIVE_PROGRESS,
            failure_type=FailureType.STRUCTURAL_FAILURE,
            failure_signals=[
                "structural mismatch in the reduction",
                "negative bound certificate from the helper",
            ],
        )

        self.assertEqual(updated.route_phase, RoutePhase.CUT)
        self.assertEqual(updated.route_decision, RouteDecision.CUT_ROUTE)
        self.assertEqual(updated.tried_families[-1].cut_reason, "structural_failure")

    def test_no_candidate_requires_expert_review_and_persistent_lane_completion(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.public_search_status = PublicSearchStatus.COMPLETE

        updated = evaluate_route(
            state,
            progress=ProgressType.NO_PROGRESS,
            consider_no_candidate=True,
        )

        self.assertEqual(updated.route_decision, RouteDecision.SPAWN_EXPERT_REVIEW)
        self.assertIn("expert_review_required", updated.no_candidate_blockers)
        self.assertIn("route_not_exhausted", updated.no_candidate_blockers)

        updated.expert_review_status = "complete"
        updated.public_search_status = "complete"
        updated.persistent_lane_status = PersistentLaneStatus.ACTIVE
        updated.no_candidate_blockers = ["open_math_boundary"]
        blocked = evaluate_route(updated, consider_no_candidate=True)

        self.assertEqual(blocked.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("persistent_lane_active", blocked.no_candidate_blockers)
        self.assertIn("open_math_boundary", blocked.no_candidate_blockers)

    def test_no_candidate_allowed_when_public_search_blocked_by_rules(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=4,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT.value,
                failure_signals=["external search blocked by contest rules; offline substitute approved"],
                exhaustion_reason="public search rule-blocked with substitute, expert and lane closed",
            )
        )
        state.public_search_status = PublicSearchStatus.BLOCKED_BY_RULES
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.ALLOW_NO_CANDIDATE)
        self.assertEqual(updated.no_candidate_blockers, [])
        self.assertEqual(updated.public_search_status, PublicSearchStatus.BLOCKED_BY_RULES)

    def test_exhausted_state_allows_no_candidate(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=4,
                failure_type="evidence_insufficient",
                failure_signals=["all planned routes exhausted"],
                exhaustion_reason="no viable family remains",
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.ALLOW_NO_CANDIDATE)
        self.assertEqual(updated.no_candidate_blockers, [])

    def test_blank_exhaustion_reason_does_not_validate_no_candidate_certificate(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=6,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT.value,
                failure_signals=["all gates closed"],
                exhaustion_reason="",
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("route_exhaustion_unproven", updated.no_candidate_blockers)

    def test_stale_or_stopped_persistent_lane_needs_stop_report_for_no_candidate(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=4,
                failure_type="evidence_insufficient",
                failure_signals=["exhausted"],
                exhaustion_reason="all gates closed",
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.STALE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("persistent_lane_stop_report_required", updated.no_candidate_blockers)

    def test_no_candidate_requires_machine_readable_exhaustion_certificate(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("route_exhaustion_unproven", updated.no_candidate_blockers)

    def test_resolved_system_blockers_do_not_prevent_no_candidate_certificate(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=5,
                failure_type=FailureType.HELPER_BOUND_LIMIT.value,
                failure_signals=["helper returned only trivial roots"],
                exhaustion_reason="all blockers resolved and lane concluded",
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.HELPER_BOUND_LIMIT
        state.no_candidate_blockers = [
            "route_not_exhausted",
            "public_search_required",
            "expert_review_required",
            "candidate_queue_not_empty",
            "persistent_lane_active",
            "helper_bound_limit",
            "same_family_stalled_twice",
            "trivial_root_repetition",
            "route_cut",
        ]

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.ALLOW_NO_CANDIDATE)
        self.assertEqual(updated.no_candidate_blockers, [])

    def test_persistent_lane_nested_blockers_prevent_no_candidate(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.persistent_lane.no_candidate_blockers = ["open algebra boundary"]
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("persistent_lane_blockers", updated.no_candidate_blockers)

    def test_representation_mismatch_is_not_swallowed_by_generic_mismatch(self):
        failure = classify_failure(
            ["representation mismatch between bytes and polynomial variables"]
        )

        self.assertEqual(failure, FailureType.REPRESENTATION_MISMATCH)

    def test_structural_representation_mixed_signal_prefers_structural(self):
        failure = classify_failure(
            [
                "representation mismatch between byte order and model",
                "structural degree mismatch in the polynomial system",
            ]
        )

        self.assertEqual(failure, FailureType.STRUCTURAL_FAILURE)

    def test_stronger_failure_type_is_not_downcast(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")
        state.failure_type = FailureType.HELPER_BOUND_LIMIT

        updated = evaluate_route(
            state,
            progress=ProgressType.NO_PROGRESS,
            failure_type=FailureType.EVIDENCE_INSUFFICIENT,
            failure_signals=["guard held duplicate low-quality candidate"],
        )

        self.assertEqual(updated.failure_type, FailureType.HELPER_BOUND_LIMIT)

    def test_classifiers_prefer_stronger_failures(self):
        progress = classify_progress(
            evidence_delta_score=0,
            challenge_advanced=False,
            negative_evidence_quality="high",
        )
        failure = classify_failure(
            [
                "helper smoke passed",
                "real instance outside helper math coverage",
                "structural mismatch in polynomial degree",
            ]
        )

        self.assertEqual(progress, ProgressType.NEGATIVE_PROGRESS)
        self.assertEqual(failure, FailureType.STRUCTURAL_FAILURE)

    def test_failure_precedence_prefers_structural_over_representation(self):
        structural_with_representation = classify_failure(
            [
                "structural degree mismatch",
                "representation mismatch between transformed variables",
            ]
        )
        representation_only = classify_failure(
            ["representation mismatch between bytes and polynomial variables"]
        )

        self.assertEqual(
            structural_with_representation,
            FailureType.STRUCTURAL_FAILURE,
        )
        self.assertEqual(representation_only, FailureType.REPRESENTATION_MISMATCH)

    def test_new_family_entries_have_auditable_route_start_contract(self):
        state = RouteState.new(current_family="crypto.lattice.small_roots")

        entry = state.ensure_family_entry()
        payload = entry.to_dict()

        self.assertEqual(payload["hypothesis"], "route family may fit available artifacts")
        self.assertEqual(payload["continue_if"], "challenge evidence delta is observed")
        self.assertEqual(
            payload["cut_if"],
            "route assumptions fail, helper bounds are exceeded, or no evidence delta repeats",
        )
        self.assertEqual(payload["expected_evidence_delta"], "new candidate evidence or qualified negative evidence")
        self.assertIsNone(payload["exhaustion_reason"])
        self.assertEqual(FamilyLedgerEntry.from_dict(payload).to_dict(), payload)

    def test_no_candidate_requires_complete_exhausted_ledger_entry(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=4,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT.value,
                failure_signals=["public search, expert review, and lane exhausted"],
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = True
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("route_exhaustion_unproven", updated.no_candidate_blockers)

        state.tried_families[-1].exhaustion_reason = "all required route gates exhausted"
        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.ALLOW_NO_CANDIDATE)

    def test_no_candidate_allows_justified_impossible_family_switch(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=4,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT.value,
                failure_signals=["no viable alternate family after review"],
                exhaustion_reason="expert review and public search found no viable alternate family",
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = False
        state.family_switch_justified_impossible = True
        state.family_switch_impossible_reason = "single artifact exposes no coherent alternate family"
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.ALLOW_NO_CANDIDATE)

    def test_no_candidate_requires_real_family_switch_or_justified_impossible(self):
        state = RouteState.new(current_family="crypto.unknown")
        state.route_phase = RoutePhase.EXHAUSTED
        state.tried_families.append(
            FamilyLedgerEntry(
                route_id="route_001",
                family="crypto.unknown",
                status="exhausted",
                ended_at_cycle=4,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT.value,
                failure_signals=["all gates closed"],
                exhaustion_reason="no viable family remains",
            )
        )
        state.public_search_status = PublicSearchStatus.COMPLETE
        state.expert_review_status = "complete"
        state.persistent_lane_status = PersistentLaneStatus.COMPLETE
        state.candidate_queue_empty = True
        state.local_baseline_done = True
        state.short_codex_done = True
        state.family_switch_done = False
        state.family_switch_justified_impossible = False
        state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

        updated = evaluate_route(state, consider_no_candidate=True)

        self.assertEqual(updated.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
        self.assertIn("family_switch_required", updated.no_candidate_blockers)

    def test_stale_or_stopped_persistent_lane_requires_stop_report_and_no_blockers(self):
        for lane_status in (PersistentLaneStatus.STALE, PersistentLaneStatus.STOPPED):
            with self.subTest(lane_status=lane_status):
                state = RouteState.new(current_family="crypto.unknown")
                state.route_phase = RoutePhase.EXHAUSTED
                state.tried_families.append(
                    FamilyLedgerEntry(
                        route_id="route_001",
                        family="crypto.unknown",
                        status="exhausted",
                        ended_at_cycle=4,
                        failure_type=FailureType.EVIDENCE_INSUFFICIENT.value,
                        failure_signals=["background lane exhausted"],
                        exhaustion_reason="background lane stopped with no remaining blockers",
                    )
                )
                state.public_search_status = PublicSearchStatus.COMPLETE
                state.expert_review_status = "complete"
                state.persistent_lane_status = lane_status
                state.persistent_lane.status = lane_status
                state.candidate_queue_empty = True
                state.local_baseline_done = True
                state.short_codex_done = True
                state.family_switch_done = True
                state.failure_type = FailureType.EVIDENCE_INSUFFICIENT

                missing_report = evaluate_route(state, consider_no_candidate=True)

                self.assertEqual(missing_report.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
                self.assertIn("persistent_lane_stop_report_required", missing_report.no_candidate_blockers)

                state.persistent_lane.stop_report_path = "state/lane-stop-report.md"
                state.persistent_lane.no_candidate_blockers = ["open blocker"]
                blocked = evaluate_route(state, consider_no_candidate=True)

                self.assertEqual(blocked.route_decision, RouteDecision.BLOCK_NO_CANDIDATE)
                self.assertIn("persistent_lane_blockers", blocked.no_candidate_blockers)

                state.persistent_lane.no_candidate_blockers = []
                allowed = evaluate_route(state, consider_no_candidate=True)

                self.assertEqual(allowed.route_decision, RouteDecision.ALLOW_NO_CANDIDATE)


if __name__ == "__main__":
    unittest.main()
