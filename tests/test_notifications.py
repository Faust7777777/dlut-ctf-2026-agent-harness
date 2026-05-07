from __future__ import annotations

import unittest
from io import StringIO
import tempfile
from unittest.mock import patch
from pathlib import Path

from ctf_agents.submit.decisions import Decision, GuardDecision, HoldReason
from ctf_agents.submit.notifications import (
    notify_decision,
    notify_force_submit_result,
    notify_submit_outcome,
)


class NotificationDispatchTest(unittest.TestCase):
    def test_human_review_decision_dispatches_notification(self) -> None:
        decision = GuardDecision(
            action=Decision.HUMAN_REVIEW,
            challenge_id="web-03",
            category="web",
            flag="flag{candidate-human-review}",
            score=0.87,
            reason="证据不足",
        )

        with patch("ctf_agents.submit.notifications.notify_human_review") as mocked:
            mocked.return_value = {"sent": False, "preview": "human"}
            out = notify_decision({"enabled": False}, decision)

        self.assertEqual(out["event"], "human_review")
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["challenge_id"], "web-03")
        self.assertEqual(kwargs["category"], "web")
        self.assertEqual(kwargs["score"], 0.87)
        self.assertIn("flag{c", kwargs["flag_redacted"])

    def test_kill_switch_decision_dispatches_notification(self) -> None:
        decision = GuardDecision(
            action=Decision.HUMAN_REVIEW,
            challenge_id="misc-01",
            category="misc",
            flag="flag{candidate-kill-switch}",
            score=0.99,
            reason="kill switch 文件存在 → 自动提交全局降级",
            hold_reason=HoldReason.KILL_SWITCH_ACTIVE,
            kill_switch_active=True,
        )

        with patch("ctf_agents.submit.notifications.notify_kill_switch") as mocked:
            mocked.return_value = {"sent": False, "preview": "kill"}
            out = notify_decision({"enabled": False}, decision)

        self.assertEqual(out["event"], "kill_switch")
        mocked.assert_called_once_with(
            {"enabled": False},
            activated=True,
            reason="kill switch 文件存在 → 自动提交全局降级",
        )

    def test_freeze_outcome_dispatches_notification_only_when_newly_frozen(self) -> None:
        decision = GuardDecision(
            action=Decision.AUTO_SUBMIT,
            challenge_id="misc-02",
            category="misc",
            flag="flag{wrong-candidate}",
        )
        outcome = {
            "wrong_count": 2,
            "newly_frozen": True,
        }

        with patch("ctf_agents.submit.notifications.notify_freeze") as mocked:
            mocked.return_value = {"sent": False, "preview": "freeze"}
            out = notify_submit_outcome(
                {"enabled": False},
                decision=decision,
                state_update=outcome,
                max_wrong=2,
                log_hint="logs/run.jsonl#L1",
            )

        self.assertEqual(out["event"], "freeze")
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["challenge_id"], "misc-02")
        self.assertEqual(kwargs["wrong_count"], 2)
        self.assertEqual(kwargs["max_wrong"], 2)

        out2 = notify_submit_outcome(
            {"enabled": False},
            decision=decision,
            state_update={"wrong_count": 1, "newly_frozen": False},
            max_wrong=2,
        )
        self.assertEqual(out2["event"], "none")

    def test_force_submit_result_dispatches_notification(self) -> None:
        with patch("ctf_agents.submit.notifications.notify_force_submit") as mocked:
            mocked.return_value = {"sent": False, "preview": "force"}
            out = notify_force_submit_result(
                {"enabled": False},
                challenge_id="web-03",
                flag="flag{force-submit}",
                correct=True,
                reason="browser confirmed",
                actor="human:cli",
            )

        self.assertEqual(out["event"], "force_submit")
        mocked.assert_called_once()
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["challenge_id"], "web-03")
        self.assertTrue(kwargs["correct"])
        self.assertEqual(kwargs["reason"], "browser confirmed")

    def test_no_notification_for_hold(self) -> None:
        decision = GuardDecision(
            action=Decision.HOLD,
            challenge_id="misc-03",
            category="misc",
            flag="flag{hold-candidate}",
            reason="low confidence",
        )

        out = notify_decision({"enabled": False}, decision)

        self.assertEqual(out["event"], "none")

    def test_force_submit_cli_commit_dispatches_notification(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        config_path = root / "configs" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            """
submit:
  adapter: dryrun
  auto_submit: true
  auto_submit_categories: [misc, forensics]
  min_conf_auto_submit: 0.92
  min_conf_human_review: 0.70
  max_wrong_per_challenge: 2
  min_seconds_between_submits_global: 25
  min_seconds_between_submits_per_challenge: 90
  flag_regex: "(?i)(flag|dlutctf)\\\\{[^{}\\\\s]{4,128}\\\\}"
  state_path: logs/submission_state.json
  kill_switch_file: .auto_submit_off
  force_submit_min_reason_length: 10
  pwn_reverse_force_human_review: true
scope: {}
feishu:
  enabled: false
""".strip(),
            encoding="utf-8",
        )
        argv = [
            "force_submit",
            "--challenge-id",
            "misc-99",
            "--flag",
            "flag{force-submit-notify}",
            "--category",
            "misc",
            "--reason",
            "manual browser confirmation",
            "--config",
            str(config_path),
            "--commit",
        ]

        with (
            patch("sys.argv", argv),
            patch("sys.stdout", new_callable=StringIO),
            patch("ctf_agents.submit.force_submit._load_adapter") as load_adapter,
            patch("ctf_agents.submit.force_submit.notify_force_submit_result") as notify_result,
        ):
            from ctf_agents.submit.force_submit import main
            from ctf_agents.submit.platform_adapter import SubmitResult

            class Adapter:
                def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
                    return SubmitResult(ok=True, correct=True, message="correct", raw={})

            load_adapter.return_value = Adapter()
            notify_result.return_value = {"event": "force_submit", "sent": False}

            rc = main()

        self.assertEqual(rc, 0)
        notify_result.assert_called_once()
        kwargs = notify_result.call_args.kwargs
        self.assertEqual(kwargs["challenge_id"], "misc-99")
        self.assertEqual(kwargs["flag"], "flag{force-submit-notify}")
        self.assertTrue(kwargs["correct"])
        self.assertEqual(kwargs["actor"], "human:cli")


if __name__ == "__main__":
    unittest.main()
