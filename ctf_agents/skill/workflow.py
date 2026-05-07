"""Skill-race workflow: route → agent → guard → adapter → log → notify.

This is the runtime that wires together the building blocks built so
far.  It does **not** contain solve logic — agents are pluggable
callables.  For dry-run we inject mock agents from
``ctf_agents.skill.agents.mock``; for real solving you'd inject Codex
or Claude-driven agents.

For every challenge processed the workflow emits a sequence of JSONL
events into ``logs/<run_id>.jsonl`` matching the schema in §9 of the
handoff plan:

  challenge_seen → route_decision → flag_candidate → submit_decision
                 → submit_result (if auto_submit) → writeup_note

Notifications fire on the same decision points that ``notify_decision``
and ``notify_submit_outcome`` care about.  Webhook absence makes them
no-op (preview only), so dry-run is safe.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Optional

from ctf_agents.common.logging_jsonl import JsonlLogger
from ctf_agents.skill.router import Challenge, route
from ctf_agents.submit.decisions import Decision, GuardDecision
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard
from ctf_agents.submit.notifications import notify_decision, notify_submit_outcome
from ctf_agents.submit.platform_adapter import DryRunAdapter, PlatformAdapter, SubmitResult


AgentFn = Callable[[Challenge], Optional[FlagCandidate]]


def _redact_for_log(flag: str) -> str:
    if not flag:
        return ""
    if len(flag) <= 14:
        return flag[:6] + "…"
    return flag[:6] + "…" + flag[-4:]


class SkillWorkflow:
    def __init__(
        self,
        project_root: str | Path,
        cfg: dict[str, Any],
        agents: dict[str, AgentFn] | None = None,
        adapter: PlatformAdapter | None = None,
        logger: JsonlLogger | None = None,
        guard: FlagGuard | None = None,
    ):
        self.project_root = Path(project_root)
        self.cfg = cfg
        submit_cfg = cfg.get("submit", {})
        self.guard = guard or FlagGuard(project_root=self.project_root, submit_cfg=submit_cfg)
        self.adapter = adapter or DryRunAdapter()
        self.logger = logger or JsonlLogger(logs_dir=str(self.project_root / "logs"))
        self.agents = agents or {}
        self._max_wrong = int(submit_cfg.get("max_wrong_per_challenge", 2))

    def process(self, challenge: Challenge) -> dict[str, Any]:
        ch_id = challenge.id
        decision_route = route(challenge)
        category = decision_route["route"]

        self.logger.event(
            event_type="challenge_seen",
            actor="workflow",
            challenge_id=ch_id,
            category=category,
            message=f"题目 {challenge.title!r} 分流到 {category}",
            data={"title": challenge.title, "router_scores": decision_route["scores"]},
            redact=False,
        )
        self.logger.event(
            event_type="route_decision",
            actor="router",
            challenge_id=ch_id,
            category=category,
            message=f"route={category} review_required={decision_route['review_required']}",
            data=decision_route,
            redact=False,
        )

        agent_fn = self.agents.get(category)
        if agent_fn is None:
            self.logger.event(
                event_type="error",
                actor="workflow",
                challenge_id=ch_id,
                category=category,
                message=f"没有为 {category} 注册 agent，跳过",
                redact=False,
            )
            return {
                "challenge_id": ch_id,
                "category": category,
                "outcome": "no_agent",
                "decision": None,
                "adapter_result": None,
                "state_update": None,
            }

        candidate = agent_fn(challenge)
        if candidate is None:
            self.logger.event(
                event_type="hypothesis",
                actor=f"agent:{category}",
                challenge_id=ch_id,
                category=category,
                message="agent 未产出 flag 候选",
                redact=False,
            )
            return {
                "challenge_id": ch_id,
                "category": category,
                "outcome": "no_candidate",
                "decision": None,
                "adapter_result": None,
                "state_update": None,
            }

        self.logger.event(
            event_type="flag_candidate",
            actor=f"agent:{category}",
            challenge_id=ch_id,
            category=category,
            message="agent 提出 flag 候选",
            data={
                "flag_redacted": _redact_for_log(candidate.flag),
                "evidence_count": candidate.evidence_count,
                "extraction_confidence": candidate.extraction_confidence,
                "agent_votes_n": len(candidate.agent_votes or []),
                "risk": candidate.risk,
            },
            confidence=candidate.extraction_confidence,
            redact=True,
        )

        decision = self.guard.decide(candidate)
        self.logger.event(
            event_type="submit_decision",
            actor="guard",
            challenge_id=ch_id,
            category=category,
            message=f"action={decision.action.value} reason={decision.reason}",
            data=decision.to_dict(),
            confidence=decision.score,
            redact=False,
        )
        notification = notify_decision(self.cfg.get("feishu", {}), decision)
        if notification.get("event") not in {"none", None}:
            self.logger.event(
                event_type="notification",
                actor="workflow",
                challenge_id=ch_id,
                category=category,
                message=f"飞书 {notification['event']}：sent={notification.get('sent', False)}",
                data={"notification": notification},
                redact=False,
            )

        adapter_result: Optional[SubmitResult] = None
        state_update: Optional[dict[str, Any]] = None
        if decision.action is Decision.AUTO_SUBMIT:
            adapter_result = self.adapter.submit_flag(candidate.challenge_id, candidate.flag)
            state_update = self.guard.record_outcome(
                candidate,
                decision,
                correct=adapter_result.correct,
                platform_response=adapter_result.message,
            )
            self.logger.event(
                event_type="submit_result",
                actor="adapter",
                challenge_id=ch_id,
                category=category,
                message=(
                    f"adapter={self.cfg.get('submit', {}).get('adapter', 'dryrun')} "
                    f"correct={adapter_result.correct} ok={adapter_result.ok}"
                ),
                data={
                    "adapter_message": adapter_result.message,
                    "state_update": state_update,
                },
                redact=True,
            )
            outcome_notification = notify_submit_outcome(
                self.cfg.get("feishu", {}),
                decision=decision,
                state_update=state_update,
                max_wrong=self._max_wrong,
            )
            if outcome_notification.get("event") not in {"none", None}:
                self.logger.event(
                    event_type="notification",
                    actor="workflow",
                    challenge_id=ch_id,
                    category=category,
                    message=f"飞书 {outcome_notification['event']}：sent={outcome_notification.get('sent', False)}",
                    data={"notification": outcome_notification},
                    redact=False,
                )

        self.logger.event(
            event_type="writeup_note",
            actor="workflow",
            challenge_id=ch_id,
            category=category,
            message=f"workflow 完成 {ch_id} (action={decision.action.value})",
            redact=False,
        )

        return {
            "challenge_id": ch_id,
            "category": category,
            "outcome": decision.action.value,
            "decision": decision.to_dict(),
            "adapter_result": (
                {
                    "ok": adapter_result.ok,
                    "correct": adapter_result.correct,
                    "message": adapter_result.message[:200],
                }
                if adapter_result
                else None
            ),
            "state_update": state_update,
        }
