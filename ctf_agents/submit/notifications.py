"""Semantic feishu notifications for guard events.

Wraps ``ctf_agents.common.feishu.send_text`` with a small set of
purpose-specific helpers.  Each helper:

  - is a no-op if ``feishu.enabled`` is false or webhook is empty
  - never logs the webhook URL or secret (those live only in ``.env``)
  - keeps message bodies under ~300 chars so a long incident doesn't
    spam the chat
  - redacts the flag (first 6 + last 4 chars only)

Call from FlagGuard's caller (the agent) at the right moments — we
intentionally do NOT auto-fire from inside FlagGuard, because the agent
loop knows context (was this the user's request? a retry? etc) that
the guard doesn't.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ctf_agents.common.feishu import send_text
from .decisions import Decision, GuardDecision, HoldReason


def _redact_flag(flag: str) -> str:
    if not flag:
        return ""
    if len(flag) <= 14:
        return flag[:6] + "…"
    return flag[:6] + "…" + flag[-4:]


def _resolve_webhook(feishu_cfg: dict[str, Any]) -> tuple[str, str, bool]:
    enabled = bool(feishu_cfg.get("enabled", False))
    webhook = os.environ.get("FEISHU_WEBHOOK", "")
    secret = os.environ.get("FEISHU_SECRET", "")
    return webhook, secret, enabled and bool(webhook)


def notify_freeze(
    feishu_cfg: dict[str, Any],
    *,
    challenge_id: str,
    category: str,
    wrong_count: int,
    max_wrong: int,
    last_flag_redacted: str,
    log_hint: str = "",
) -> dict:
    webhook, secret, do_send = _resolve_webhook(feishu_cfg)
    msg = (
        "[DLUT-CTF] 题目冻结\n"
        f"题目: {challenge_id} / {category}\n"
        f"错误次数: {wrong_count}/{max_wrong}\n"
        "原因: max_wrong_reached\n"
        f"最后候选: {last_flag_redacted}\n"
        + (f"日志: {log_hint}\n" if log_hint else "")
        + "解除: force_submit:<id>:<flag>:<reason> 或 manual_unfreeze"
    )
    if not do_send:
        return {"sent": False, "reason": "feishu disabled or webhook missing", "preview": msg}
    return {"sent": True, **send_text(webhook, msg, secret=secret), "preview": msg}


def notify_human_review(
    feishu_cfg: dict[str, Any],
    *,
    challenge_id: str,
    category: str,
    score: float,
    flag_redacted: str,
    reason: str,
) -> dict:
    webhook, secret, do_send = _resolve_webhook(feishu_cfg)
    msg = (
        "[DLUT-CTF] 需要人审\n"
        f"题目: {challenge_id} / {category}\n"
        f"事件: flag_candidate\n"
        f"候选: {flag_redacted}\n"
        f"置信度: {score:.2f}\n"
        f"原因: {reason}\n"
        "操作: 浏览器确认后回 force_submit:<id>:<flag>:<reason>"
    )
    if not do_send:
        return {"sent": False, "reason": "feishu disabled or webhook missing", "preview": msg}
    return {"sent": True, **send_text(webhook, msg, secret=secret), "preview": msg}


def notify_kill_switch(
    feishu_cfg: dict[str, Any],
    *,
    activated: bool,
    reason: str = "",
) -> dict:
    webhook, secret, do_send = _resolve_webhook(feishu_cfg)
    if activated:
        msg = (
            "[DLUT-CTF] kill switch ACTIVE\n"
            "所有 auto_submit → human_review\n"
            + (f"原因: {reason}\n" if reason else "")
            + "解除: rm <project>/.auto_submit_off"
        )
    else:
        msg = "[DLUT-CTF] kill switch 已解除，自动提交恢复"
    if not do_send:
        return {"sent": False, "reason": "feishu disabled or webhook missing", "preview": msg}
    return {"sent": True, **send_text(webhook, msg, secret=secret), "preview": msg}


def notify_force_submit(
    feishu_cfg: dict[str, Any],
    *,
    challenge_id: str,
    flag_redacted: str,
    correct: Optional[bool],
    reason: str,
    actor: str,
) -> dict:
    webhook, secret, do_send = _resolve_webhook(feishu_cfg)
    outcome = "正确" if correct is True else ("错误" if correct is False else "未知")
    msg = (
        "[DLUT-CTF] force_submit override\n"
        f"题目: {challenge_id}\n"
        f"候选: {flag_redacted}\n"
        f"结果: {outcome}\n"
        f"由: {actor}\n"
        f"原因: {reason}"
    )
    if not do_send:
        return {"sent": False, "reason": "feishu disabled or webhook missing", "preview": msg}
    return {"sent": True, **send_text(webhook, msg, secret=secret), "preview": msg}


def notify_decision(feishu_cfg: dict[str, Any], decision: GuardDecision) -> dict[str, Any]:
    """Dispatch a guard decision to the matching Feishu notification.

    HOLD/AUTO_SUBMIT/REJECT are normally log-only. HUMAN_REVIEW is the
    user-facing path, with a special template for kill switch downgrades.
    """
    if (
        decision.action is Decision.HUMAN_REVIEW
        and decision.hold_reason is HoldReason.KILL_SWITCH_ACTIVE
    ):
        return {
            "event": "kill_switch",
            **notify_kill_switch(
                feishu_cfg,
                activated=True,
                reason=decision.reason,
            ),
        }

    if decision.action is Decision.HUMAN_REVIEW:
        return {
            "event": "human_review",
            **notify_human_review(
                feishu_cfg,
                challenge_id=decision.challenge_id,
                category=decision.category,
                score=decision.score,
                flag_redacted=_redact_flag(decision.flag),
                reason=decision.reason or ",".join(decision.notes),
            ),
        }

    return {"event": "none", "sent": False, "reason": "decision does not notify"}


def notify_submit_outcome(
    feishu_cfg: dict[str, Any],
    *,
    decision: GuardDecision,
    state_update: dict[str, Any],
    max_wrong: int,
    log_hint: str = "",
) -> dict[str, Any]:
    """Notify on post-submit state transitions.

    Currently only newly-frozen challenges are chat-worthy. Normal correct
    submits stay in JSONL logs to avoid notification noise.
    """
    if not state_update.get("newly_frozen"):
        return {"event": "none", "sent": False, "reason": "no notify-worthy outcome"}
    return {
        "event": "freeze",
        **notify_freeze(
            feishu_cfg,
            challenge_id=decision.challenge_id,
            category=decision.category,
            wrong_count=int(state_update.get("wrong_count", 0)),
            max_wrong=max_wrong,
            last_flag_redacted=_redact_flag(decision.flag),
            log_hint=log_hint,
        ),
    }


def notify_force_submit_result(
    feishu_cfg: dict[str, Any],
    *,
    challenge_id: str,
    flag: str,
    correct: Optional[bool],
    reason: str,
    actor: str,
) -> dict[str, Any]:
    return {
        "event": "force_submit",
        **notify_force_submit(
            feishu_cfg,
            challenge_id=challenge_id,
            flag_redacted=_redact_flag(flag),
            correct=correct,
            reason=reason,
            actor=actor,
        ),
    }


def preview_message(kind: str, **kwargs: Any) -> str:
    """Return what would be sent without actually contacting Feishu.

    Used by the dry-run script so we can verify message templates
    without configuring a webhook.
    """
    fake_cfg = {"enabled": False}
    if kind == "freeze":
        return notify_freeze(fake_cfg, **kwargs)["preview"]
    if kind == "human_review":
        return notify_human_review(fake_cfg, **kwargs)["preview"]
    if kind == "kill_switch":
        return notify_kill_switch(fake_cfg, **kwargs)["preview"]
    if kind == "force_submit":
        return notify_force_submit(fake_cfg, **kwargs)["preview"]
    raise ValueError(f"unknown kind: {kind}")
