"""State-machine coverage for FlagGuard.

The contract under test (matches §4 of the handoff plan):
  - REJECT  on empty/format-bad flag
  - HOLD    on score < min_conf_human_review
  - HUMAN_REVIEW when score is in the [human_review, auto_submit) band
  - HUMAN_REVIEW when category is pwn/reverse, even at high score
  - HUMAN_REVIEW when kill switch file exists, even at high score
  - HUMAN_REVIEW when challenge is frozen, regardless of score
  - HUMAN_REVIEW when category is not in auto_submit_categories
  - HOLD    when global / per-challenge rate window not yet elapsed
  - AUTO_SUBMIT only when *all* gates pass
  - frozen flag persists across guard reloads
  - second wrong submission triggers freeze (max_wrong_per_challenge=2)
  - force_submit overrides score / frozen / category gates but
    still honours rate-limit + reason-length + format
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from ctf_agents.submit.decisions import Decision, HoldReason, RejectReason
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard
from ctf_agents.submit.kill_switch import activate, deactivate
from ctf_agents.submit.state_store import SubmissionStateStore


def _base_cfg(tmp_root: Path) -> dict:
    return {
        "auto_submit": True,
        "auto_submit_categories": ["misc", "forensics"],
        "min_conf_auto_submit": 0.92,
        "min_conf_human_review": 0.70,
        "max_wrong_per_challenge": 2,
        "min_seconds_between_submits_global": 25,
        "min_seconds_between_submits_per_challenge": 90,
        "flag_regex": r"(?i)(flag|dlutctf)\{[^{}\s]{4,128}\}",
        "state_path": str(tmp_root / "logs" / "submission_state.json"),
        "kill_switch_file": ".auto_submit_off",
        "force_submit_min_reason_length": 10,
        "pwn_reverse_force_human_review": True,
    }


def _high_conf_cand(challenge_id: str, category: str = "misc") -> FlagCandidate:
    return FlagCandidate(
        challenge_id=challenge_id,
        flag="flag{abcdef-high-confidence-evidence}",
        category=category,
        evidence_count=4,
        extraction_confidence=1.0,
        agent_votes=["flag{abcdef-high-confidence-evidence}"] * 3,
    )


class FlagGuardStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.cfg = _base_cfg(self.root)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _guard(self) -> FlagGuard:
        return FlagGuard(project_root=self.root, submit_cfg=self.cfg)

    def test_empty_flag_rejected(self):
        cand = FlagCandidate(challenge_id="m-1", flag="", category="misc")
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.REJECT)
        self.assertIs(d.reject_reason, RejectReason.EMPTY_FLAG)

    def test_bad_format_rejected(self):
        cand = FlagCandidate(challenge_id="m-1", flag="not-a-flag", category="misc")
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.REJECT)
        self.assertIs(d.reject_reason, RejectReason.FORMAT_INVALID)

    def test_low_confidence_held(self):
        cand = FlagCandidate(
            challenge_id="m-1",
            flag="flag{shaky-low}",
            category="misc",
            evidence_count=0,
            extraction_confidence=0.0,
        )
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.HOLD)
        self.assertIs(d.hold_reason, HoldReason.LOW_CONFIDENCE)

    def test_mid_confidence_human_review(self):
        cand = FlagCandidate(
            challenge_id="m-1",
            flag="flag{some-evidence-mid}",
            category="misc",
            evidence_count=3,
            extraction_confidence=0.6,
            agent_votes=["flag{some-evidence-mid}"],
        )
        d = self._guard().decide(cand)
        # score ≈ 0.79, sits in [0.70, 0.92) → HUMAN_REVIEW
        self.assertIs(d.action, Decision.HUMAN_REVIEW)
        self.assertGreaterEqual(d.score, 0.70)
        self.assertLess(d.score, 0.92)

    def test_high_confidence_misc_auto_submit(self):
        cand = _high_conf_cand("m-1", category="misc")
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.AUTO_SUBMIT)
        self.assertGreaterEqual(d.score, 0.92)

    def test_high_confidence_web_human_review(self):
        cand = _high_conf_cand("w-1", category="web")
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.HUMAN_REVIEW)
        self.assertIs(d.hold_reason, HoldReason.CATEGORY_NOT_AUTO)

    def test_high_confidence_pwn_human_review(self):
        cand = _high_conf_cand("p-1", category="pwn")
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.HUMAN_REVIEW)
        self.assertIs(d.hold_reason, HoldReason.PWN_REVERSE_FORCED_HUMAN)

    def test_high_confidence_reverse_human_review(self):
        cand = _high_conf_cand("r-1", category="reverse")
        d = self._guard().decide(cand)
        self.assertIs(d.action, Decision.HUMAN_REVIEW)
        self.assertIs(d.hold_reason, HoldReason.PWN_REVERSE_FORCED_HUMAN)

    def test_kill_switch_downgrades_auto_to_human_review(self):
        ks_path = self.root / self.cfg["kill_switch_file"]
        activate(ks_path, reason="testing")
        try:
            cand = _high_conf_cand("m-1", category="misc")
            d = self._guard().decide(cand)
            self.assertIs(d.action, Decision.HUMAN_REVIEW)
            self.assertIs(d.hold_reason, HoldReason.KILL_SWITCH_ACTIVE)
            self.assertTrue(d.kill_switch_active)
        finally:
            deactivate(ks_path)

    def test_global_rate_limit_holds_high_confidence(self):
        guard = self._guard()
        cand = _high_conf_cand("m-1", category="misc")
        d1 = guard.decide(cand)
        self.assertIs(d1.action, Decision.AUTO_SUBMIT)
        guard.record_outcome(cand, d1, correct=True)
        cand_b = _high_conf_cand("m-2", category="misc")
        d2 = guard.decide(cand_b)
        self.assertIs(d2.action, Decision.HOLD)
        self.assertIs(d2.hold_reason, HoldReason.RATE_LIMIT_GLOBAL)

    def test_per_challenge_rate_limit_holds(self):
        guard = self._guard()
        cand = _high_conf_cand("m-1", category="misc")
        d1 = guard.decide(cand)
        guard.record_outcome(cand, d1, correct=False)

        # Anchor timestamps to "26 s ago" so the global window (25 s)
        # has elapsed but the per-challenge window (90 s) has not.
        # Wall-clock based, so this is straightforward — no need to
        # compensate for a fresh process's near-zero monotonic clock.
        store = guard.state_store
        snap = store.snapshot()
        anchor = time.time() - 26.0
        snap["global_last_submit_unix"] = anchor
        snap["challenges"]["m-1"]["last_submit_unix"] = anchor
        from ctf_agents.submit.state_store import _atomic_write_json
        _atomic_write_json(store.state_path, snap)

        d2 = guard.decide(cand)
        self.assertIs(d2.action, Decision.HOLD)
        self.assertIs(d2.hold_reason, HoldReason.RATE_LIMIT_PER_CHALLENGE)
        self.assertEqual(d2.wrong_count, 1)

    def test_freeze_after_two_wrong(self):
        guard = self._guard()
        cand = _high_conf_cand("m-1", category="misc")
        d1 = guard.decide(cand)
        out1 = guard.record_outcome(cand, d1, correct=False)
        self.assertEqual(out1["wrong_count"], 1)
        self.assertFalse(out1["frozen"])

        # Wipe rate-limit anchors so the second decide can claim a slot
        store = guard.state_store
        snap = store.snapshot()
        snap["global_last_submit_unix"] = 0.0
        snap["challenges"]["m-1"]["last_submit_unix"] = 0.0
        from ctf_agents.submit.state_store import _atomic_write_json
        _atomic_write_json(store.state_path, snap)

        d2 = guard.decide(cand)
        self.assertIs(d2.action, Decision.AUTO_SUBMIT)
        out2 = guard.record_outcome(cand, d2, correct=False)
        self.assertEqual(out2["wrong_count"], 2)
        self.assertTrue(out2["frozen"])
        self.assertTrue(out2["newly_frozen"])

        d3 = guard.decide(cand)
        self.assertIs(d3.action, Decision.HUMAN_REVIEW)
        self.assertTrue(d3.frozen)

    def test_force_submit_reason_too_short_rejected(self):
        cand = _high_conf_cand("m-1", category="misc")
        d = self._guard().decide(cand, force_submit=True, force_reason="short")
        self.assertIs(d.action, Decision.REJECT)
        self.assertIn("force_submit_reason_too_short", d.notes)

    def test_force_submit_overrides_frozen(self):
        guard = self._guard()
        guard.state_store.force_freeze("m-1", reason="seeded for test")
        cand = _high_conf_cand("m-1", category="misc")
        d = guard.decide(
            cand,
            force_submit=True,
            force_reason="manually verified via browser devtools",
        )
        self.assertIs(d.action, Decision.AUTO_SUBMIT)
        self.assertTrue(d.frozen)
        self.assertTrue(d.force_submit)
        self.assertIn("force_submit_override", d.notes)

    def test_force_submit_overrides_category_gate(self):
        cand = _high_conf_cand("p-1", category="pwn")
        d = self._guard().decide(
            cand,
            force_submit=True,
            force_reason="exploit verified locally with libc dump",
        )
        self.assertIs(d.action, Decision.AUTO_SUBMIT)
        self.assertTrue(d.force_submit)

    def test_force_submit_still_blocked_by_rate_limit(self):
        guard = self._guard()
        cand_a = _high_conf_cand("m-1", category="misc")
        d1 = guard.decide(cand_a)
        guard.record_outcome(cand_a, d1, correct=True)

        cand_b = _high_conf_cand("m-2", category="misc")
        d2 = guard.decide(
            cand_b,
            force_submit=True,
            force_reason="manually confirmed answer for m-2",
        )
        self.assertIs(d2.action, Decision.HOLD)
        self.assertIs(d2.hold_reason, HoldReason.RATE_LIMIT_GLOBAL)
        self.assertIn("force_submit_rate_limited_global", d2.notes)

    def test_state_persists_across_guard_reloads(self):
        guard1 = self._guard()
        cand = _high_conf_cand("m-9", category="misc")
        d1 = guard1.decide(cand)
        guard1.record_outcome(cand, d1, correct=False)
        # Reload a fresh guard reading the same state file
        guard2 = self._guard()
        self.assertEqual(guard2.state_store.wrong_count("m-9"), 1)

    def test_concurrent_auto_submit_only_one_wins(self):
        """Two decide() calls in succession on different challenges:
        the first claims the global rate-limit slot, the second sees
        the anchor and is held with RATE_LIMIT_GLOBAL.  Critical race
        case — the older skeleton would have allowed both to AUTO_SUBMIT.
        """
        guard = self._guard()
        cand_a = _high_conf_cand("m-A", category="misc")
        cand_b = _high_conf_cand("m-B", category="misc")
        d_a = guard.decide(cand_a)
        # Note: NO record_outcome between the two calls — the claim
        # made inside decide() must be sufficient on its own.
        d_b = guard.decide(cand_b)
        self.assertIs(d_a.action, Decision.AUTO_SUBMIT)
        self.assertIs(d_b.action, Decision.HOLD)
        self.assertIs(d_b.hold_reason, HoldReason.RATE_LIMIT_GLOBAL)

    def test_state_durable_across_simulated_restart(self):
        """A previous process recorded a submit at wall-clock time T.
        A fresh process starting later must treat T as the rate-limit
        anchor, not be tricked by a near-zero monotonic clock."""
        guard1 = self._guard()
        cand = _high_conf_cand("m-1", category="misc")
        d1 = guard1.decide(cand)
        guard1.record_outcome(cand, d1, correct=True)

        # Simulate "fresh process" — new guard, same state file
        guard2 = self._guard()
        cand_b = _high_conf_cand("m-2", category="misc")
        d2 = guard2.decide(cand_b)
        # Global rate limit should still apply because the wall-clock
        # anchor persists.  Old monotonic-based code blocked forever
        # here; new wall-clock code blocks for ≤25 s.
        self.assertIs(d2.action, Decision.HOLD)
        self.assertIs(d2.hold_reason, HoldReason.RATE_LIMIT_GLOBAL)

    def test_state_with_far_past_timestamps_does_not_block(self):
        """Stale state with anchors from an hour ago must NOT block
        a fresh submit.  This is the symmetric correctness check to the
        durability test above."""
        store = SubmissionStateStore(self.cfg["state_path"])
        an_hour_ago = time.time() - 3600.0
        snap = {
            "global_last_submit_unix": an_hour_ago,
            "global_last_submit_iso": "2026-05-07T08:00:00+00:00",
            "challenges": {
                "m-1": {
                    "wrong_count": 0,
                    "frozen": False,
                    "frozen_at_iso": None,
                    "last_submit_unix": an_hour_ago,
                    "last_submit_iso": "2026-05-07T08:00:00+00:00",
                    "submits": [],
                }
            },
        }
        from ctf_agents.submit.state_store import _atomic_write_json
        _atomic_write_json(store.state_path, snap)

        guard = self._guard()
        cand = _high_conf_cand("m-1", category="misc")
        d = guard.decide(cand)
        self.assertIs(d.action, Decision.AUTO_SUBMIT)

    def test_wall_clock_jumping_backward_is_conservative(self):
        """If a persisted anchor is *ahead* of the current wall clock
        (NTP step / WSL sleep), the guard waits the full window rather
        than allowing an unlimited burst.  Conservative is the right
        default for a submission-rate red line."""
        store = SubmissionStateStore(self.cfg["state_path"])
        future = time.time() + 600.0  # 10 min "in the future"
        snap = {
            "global_last_submit_unix": future,
            "global_last_submit_iso": "2026-05-07T11:00:00+00:00",
            "challenges": {
                "m-1": {
                    "wrong_count": 0,
                    "frozen": False,
                    "frozen_at_iso": None,
                    "last_submit_unix": future,
                    "last_submit_iso": "2026-05-07T11:00:00+00:00",
                    "submits": [],
                }
            },
        }
        from ctf_agents.submit.state_store import _atomic_write_json
        _atomic_write_json(store.state_path, snap)

        guard = self._guard()
        cand = _high_conf_cand("m-1", category="misc")
        d = guard.decide(cand)
        self.assertIs(d.action, Decision.HOLD)
        self.assertIn(
            d.hold_reason,
            {HoldReason.RATE_LIMIT_GLOBAL, HoldReason.RATE_LIMIT_PER_CHALLENGE},
        )

    def test_corrupt_state_file_rotates_and_raises(self):
        bad_path = self.root / "logs" / "submission_state.json"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("{ this is not json", encoding="utf-8")
        store = SubmissionStateStore(bad_path)
        with self.assertRaises(RuntimeError):
            store.is_frozen("anything")
        self.assertTrue((bad_path.parent / "submission_state.json.corrupt").exists())


class KillSwitchTest(unittest.TestCase):
    def test_activate_and_deactivate(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / ".auto_submit_off"
            from ctf_agents.submit.kill_switch import activate, deactivate, is_active
            self.assertFalse(is_active(p))
            activate(p, reason="emergency")
            self.assertTrue(is_active(p))
            self.assertTrue(deactivate(p))
            self.assertFalse(is_active(p))


if __name__ == "__main__":
    unittest.main()
