"""Skill-race workflow integration tests.

These exercise the full pipeline from router through guard to adapter,
without any external dependencies (no Feishu webhook, no real CTF
platform). Each test pins a specific decision branch and checks both
the returned summary and the JSONL event log.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from ctf_agents.common.logging_jsonl import JsonlLogger
from ctf_agents.skill.agents.mock import (
    make_bad_format_agent,
    make_low_confidence_agent,
    make_mock_agent,
    make_silent_agent,
)
from ctf_agents.skill.router import Challenge
from ctf_agents.skill.workflow import SkillWorkflow
from ctf_agents.submit.platform_adapter import DryRunAdapter, SubmitResult


def _base_cfg(state_path: Path) -> dict:
    return {
        "submit": {
            "auto_submit": True,
            "auto_submit_categories": ["misc", "forensics"],
            "min_conf_auto_submit": 0.92,
            "min_conf_human_review": 0.70,
            "max_wrong_per_challenge": 2,
            "min_seconds_between_submits_global": 25,
            "min_seconds_between_submits_per_challenge": 90,
            "flag_regex": r"(?i)(flag|dlutctf)\{[^{}\s]{4,128}\}",
            "state_path": str(state_path),
            "kill_switch_file": ".auto_submit_off",
            "force_submit_min_reason_length": 10,
            "pwn_reverse_force_human_review": True,
        },
        "feishu": {"enabled": False},
    }


class _RecordingAdapter:
    def __init__(self, correct: bool | None = True):
        self.calls: list[tuple[str, str]] = []
        self._correct = correct

    def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        self.calls.append((challenge_id, flag))
        return SubmitResult(
            ok=True,
            correct=self._correct,
            message=f"recorded {flag} for {challenge_id}",
            raw={},
        )


def _read_events(log: JsonlLogger) -> list[dict]:
    if not log.path.exists():
        return []
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines() if line.strip()]


class SkillWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_path = self.root / "logs" / "submission_state.json"
        self.cfg = _base_cfg(self.state_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self, agents: dict, adapter=None) -> SkillWorkflow:
        logger = JsonlLogger(logs_dir=str(self.root / "logs"), run_id="test-run")
        return SkillWorkflow(
            project_root=self.root,
            cfg=self.cfg,
            agents=agents,
            adapter=adapter or _RecordingAdapter(correct=True),
            logger=logger,
        )

    def test_misc_high_conf_auto_submits(self):
        adapter = _RecordingAdapter(correct=True)
        wf = self._build({"misc": make_mock_agent("misc", "flag{ok-misc}")}, adapter=adapter)
        ch = Challenge(id="m-1", title="zip stego", category="misc")
        result = wf.process(ch)

        self.assertEqual(result["outcome"], "auto_submit")
        self.assertEqual(adapter.calls, [("m-1", "flag{ok-misc}")])
        self.assertTrue(result["adapter_result"]["correct"])
        events = _read_events(wf.logger)
        types = [e["event_type"] for e in events]
        for required in ("challenge_seen", "route_decision", "flag_candidate", "submit_decision", "submit_result", "writeup_note"):
            self.assertIn(required, types)

    def test_concurrent_misc_then_forensics_holds_second(self):
        """Wires up the race-fix end-to-end: first agent claims the
        global window, second agent sees rate-limit hold even though
        its own evidence is high-confidence."""
        adapter = _RecordingAdapter(correct=True)
        wf = self._build(
            {
                "misc": make_mock_agent("misc", "flag{ok-misc-1}"),
                "forensics": make_mock_agent("forensics", "flag{ok-forensics-1}"),
            },
            adapter=adapter,
        )
        r1 = wf.process(Challenge(id="m-1", title="zip", category="misc"))
        r2 = wf.process(Challenge(id="f-1", title="pcap", category="forensics"))

        self.assertEqual(r1["outcome"], "auto_submit")
        self.assertEqual(r2["outcome"], "hold")
        self.assertEqual(r2["decision"]["hold_reason"], "rate_limit_global")
        self.assertEqual(len(adapter.calls), 1)

    def test_web_high_conf_human_review(self):
        wf = self._build({"web": make_mock_agent("web", "flag{ok-web}")})
        ch = Challenge(id="w-1", title="SSTI 注入", category="web")
        result = wf.process(ch)
        self.assertEqual(result["outcome"], "human_review")
        self.assertEqual(result["decision"]["hold_reason"], "category_not_auto")

    def test_pwn_high_conf_human_review_forced(self):
        wf = self._build({"pwn": make_mock_agent("pwn", "flag{ok-pwn}")})
        ch = Challenge(id="p-1", title="ret2libc", category="pwn")
        result = wf.process(ch)
        self.assertEqual(result["outcome"], "human_review")
        self.assertEqual(result["decision"]["hold_reason"], "pwn_reverse_forced_human")

    def test_low_confidence_holds(self):
        wf = self._build({"misc": make_low_confidence_agent("misc", "flag{shaky}")})
        ch = Challenge(id="m-2", title="模糊 misc", category="misc")
        result = wf.process(ch)
        self.assertEqual(result["outcome"], "hold")
        self.assertEqual(result["decision"]["hold_reason"], "low_confidence")

    def test_bad_format_rejected(self):
        wf = self._build({"misc": make_bad_format_agent("misc")})
        ch = Challenge(id="m-3", title="bad fmt", category="misc")
        result = wf.process(ch)
        self.assertEqual(result["outcome"], "reject")
        self.assertEqual(result["decision"]["reject_reason"], "format_invalid")

    def test_silent_agent_no_candidate(self):
        wf = self._build({"misc": make_silent_agent()})
        ch = Challenge(id="m-4", title="silent", category="misc")
        result = wf.process(ch)
        self.assertEqual(result["outcome"], "no_candidate")
        self.assertIsNone(result["decision"])

    def test_no_agent_for_category(self):
        wf = self._build({})
        ch = Challenge(id="r-1", title="某逆向题", category="reverse")
        result = wf.process(ch)
        self.assertEqual(result["outcome"], "no_agent")

    def test_freeze_after_two_wrong_submits(self):
        adapter = _RecordingAdapter(correct=False)
        wf = self._build({"misc": make_mock_agent("misc", "flag{wrong-misc}")}, adapter=adapter)

        r1 = wf.process(Challenge(id="m-9", title="z", category="misc"))
        self.assertEqual(r1["outcome"], "auto_submit")
        self.assertEqual(r1["state_update"]["wrong_count"], 1)
        self.assertFalse(r1["state_update"]["frozen"])

        # Wipe rate-limit anchors so the second decide can claim
        snap = json.loads(self.state_path.read_text(encoding="utf-8"))
        snap["global_last_submit_unix"] = 0.0
        snap["challenges"]["m-9"]["last_submit_unix"] = 0.0
        from ctf_agents.submit.state_store import _atomic_write_json
        _atomic_write_json(self.state_path, snap)

        r2 = wf.process(Challenge(id="m-9", title="z again", category="misc"))
        self.assertEqual(r2["outcome"], "auto_submit")
        self.assertEqual(r2["state_update"]["wrong_count"], 2)
        self.assertTrue(r2["state_update"]["frozen"])
        self.assertTrue(r2["state_update"]["newly_frozen"])

        r3 = wf.process(Challenge(id="m-9", title="z third", category="misc"))
        self.assertEqual(r3["outcome"], "human_review")
        self.assertTrue(r3["decision"]["frozen"])


if __name__ == "__main__":
    unittest.main()
