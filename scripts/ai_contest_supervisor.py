#!/usr/bin/env python3
"""AI-identity contest supervisor.

Per docs/opus_ai_identity_handoff.md §"P0-2: AI Contest Supervisor",
this is the **deterministic** state machine that runs unattended after
the operator's single starting prompt.  It is intentionally not an
LLM-driven loop: every flag-submit decision must traverse the local
``FlagGuard`` and every platform call must traverse ``GZCTFAdapter``.

Loop cadence:
    1. Sync challenge list (game/details).
    2. For each new/incomplete challenge:
       - fetch detail
       - download attachment if static
       - dispatch a registered safe agent (Misc/Forensics)
       - normalize candidate → FlagCandidate
       - guard.decide()
       - if AUTO_SUBMIT: claim local lock, call adapter, poll status,
         record_outcome
    3. Persist updated state.
    4. Heartbeat.
    5. Sleep `challenge_loop_interval_s`, repeat.

Hard rules (mirrored from spec):
    - duplicate (challenge_id, flag_hash) is never resubmitted
    - WrongAnswer → freeze the challenge (max_wrong_per_challenge=1)
    - CheatDetected → globally disable further submits
    - Accepted → mark complete, never touch again
    - FlagSubmitted timeout → do NOT resubmit; rely on next-tick polling
    - on restart, accepted/frozen/in-flight challenges keep their state

The supervisor is a long-running process.  It exits 0 on
``global_run_timeout_s`` reached, on operator SIGINT, or on
unrecoverable platform error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.common.logging_jsonl import JsonlLogger  # noqa: E402
from ctf_agents.common.yaml_compat import safe_load_file  # noqa: E402
from ctf_agents.contest.route_control import (  # noqa: E402
    ExpertReviewStatus,
    FailureType,
    PersistentLaneStatus,
    ProgressType,
    PublicSearchStatus,
    RouteDecision,
    RoutePhase,
    SearchLedgerEntry,
    RouteState,
    can_emit_no_candidate,
    classify_failure,
    classify_progress,
    evaluate_route,
)
from ctf_agents.sidecar.codex_validator import (  # noqa: E402
    is_safe_artifact_path,
    validate_codex_candidate,
)
from ctf_agents.skill.agents.misc_real import real_misc_agent  # noqa: E402
from ctf_agents.skill.router import Challenge  # noqa: E402
from ctf_agents.submit.decisions import Decision, GuardDecision  # noqa: E402
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard  # noqa: E402
from ctf_agents.submit.gzctf_adapter import GZCTFAdapter  # noqa: E402
from ctf_agents.submit.notifications import (  # noqa: E402
    notify_decision,
    notify_kill_switch,
    notify_submit_outcome,
)

logger = logging.getLogger("ai_contest_supervisor")

try:
    from dotenv import load_dotenv  # type: ignore  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - exercised by import tests
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


load_dotenv(PROJECT / ".env")


# ---- state schema ---------------------------------------------------

CHALLENGE_STATE_DISCOVERED = "discovered"
CHALLENGE_STATE_DETAIL_FETCHED = "detail_fetched"
CHALLENGE_STATE_DOWNLOADED = "downloaded"
CHALLENGE_STATE_NO_AGENT = "no_agent"
CHALLENGE_STATE_NO_CANDIDATE = "no_candidate"
CHALLENGE_STATE_PENDING = "pending"
CHALLENGE_STATE_ACCEPTED = "accepted"
CHALLENGE_STATE_WRONG_FROZEN = "wrong_frozen"
CHALLENGE_STATE_CHEAT_FROZEN = "cheat_frozen"
CHALLENGE_STATE_NOTFOUND = "platform_notfound"
CHALLENGE_TERMINAL_STATES = {
    CHALLENGE_STATE_ACCEPTED,
    CHALLENGE_STATE_WRONG_FROZEN,
    CHALLENGE_STATE_CHEAT_FROZEN,
}


def _flag_hash(flag: str) -> str:
    return hashlib.sha256((flag or "").strip().encode("utf-8")).hexdigest()[:16]


def _redact_flag_for_state(flag: str) -> str:
    if not flag:
        return ""
    if len(flag) <= 14:
        return flag[:6] + "…"
    return flag[:6] + "…" + flag[-4:]


def _sanitize_decision_for_log(decision_dict: dict) -> dict:
    """Strip the plaintext flag from a guard-decision dict before
    writing it to JSONL.  ``GuardDecision.to_dict()`` keeps the flag
    field so downstream callers can consume the full decision object,
    but persistent logs must never carry it (Codex review §1).  The
    JsonlLogger's redact pattern targets auth tokens / cookies and
    does not match flag-shaped strings, so the only way to keep flags
    out of logs is to redact upstream.
    """
    sanitized = dict(decision_dict or {})
    flag = sanitized.pop("flag", None)
    if flag:
        sanitized["flag_redacted"] = _redact_flag_for_state(flag)
        sanitized["flag_hash"] = _flag_hash(flag)
    return sanitized


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT))
    except ValueError:
        return str(path)


def _maybe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _empty_challenge_state(challenge_id: str) -> dict:
    return {
        "challenge_id": str(challenge_id),
        "title": "",
        "category": "",
        "type": "",
        "state": CHALLENGE_STATE_DISCOVERED,
        "attachment_paths": [],
        "candidates": [],
        "submitted_flag_hashes": [],
        "accepted_flag_hash": None,
        "wrong_count": 0,
        "freeze_reason": None,
        "last_submit_id": None,
        "last_update": _utcnow_iso(),
        "route_control": _default_route_control_state(),
        "route_control_action_state": _default_route_control_action_state(),
    }


def _default_route_control_state() -> dict:
    return RouteState.new(current_family="misc.initial").to_dict()


def _default_route_control_action_state() -> dict:
    return {
        "public_search": {"status": "not_requested", "request_path": None, "requested_at": None},
        "expert_review": {"status": "not_requested", "request_path": None, "requested_at": None},
        "persistent_lane": {
            "status": "not_started",
            "request_path": None,
            "requested_at": None,
            "result_path": None,
            "consumed_at": None,
            "conclusion": None,
        },
        "family_switch": {
            "status": "not_started",
            "from_family": None,
            "to_family": None,
            "switched_at": None,
        },
    }


# ---- agent registry -------------------------------------------------

AgentFn = Callable[[Challenge], Optional[FlagCandidate]]

DEFAULT_AGENT_REGISTRY: dict[str, AgentFn] = {
    "misc": real_misc_agent,
    "forensics": real_misc_agent,
}

# Runtime capability gates are intentionally a code-level contract, not
# a YAML knob.  The supervisor soft-demotes only those auto-submit
# categories that are missing the required local solver stack.
CATEGORY_REQUIRED_CAPS: dict[str, tuple[str, ...]] = {
    "crypto": ("crypto_lattice", "crypto_classic"),  # any-of
    "pwn": ("pwn",),
    "web": ("web_static",),
}


# ---- supervisor -----------------------------------------------------


class AIContestSupervisor:
    def __init__(
        self,
        cfg: dict,
        adapter: GZCTFAdapter,
        guard: FlagGuard,
        logger_obj: Optional[JsonlLogger] = None,
        agents: Optional[dict[str, AgentFn]] = None,
        clock: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.adapter = adapter
        self.guard = guard
        self.feishu_cfg = cfg.get("feishu", {}) or {}
        self._clock = clock
        self._sleep = sleep_fn

        gz = cfg.get("gzctf", {})
        self.game_id = int(gz.get("game_id", 0))
        if not self.game_id:
            raise RuntimeError("gzctf.game_id is required")
        self.adapter.set_active_game(self.game_id)
        self.poll_timeout_s = float(gz.get("poll_timeout_s", 90.0))
        self.poll_interval_s = float(gz.get("poll_interval_s", 3.0))

        agent_cfg = cfg.get("agent", {})
        self.enabled_categories = set(
            (c.lower() for c in agent_cfg.get("enabled_categories") or [])
        )
        self.loop_interval_s = float(agent_cfg.get("challenge_loop_interval_s", 30.0))
        self.global_timeout_s = float(agent_cfg.get("global_run_timeout_s", 14400.0))
        self.heartbeat_interval_s = float(agent_cfg.get("heartbeat_interval_s", 60.0))

        # Codex sidecar (P2).  See runbooks/codex_sidecar.md.  Default
        # off; when on, the supervisor reads
        # artifacts/challenges/<id>/codex_candidates.json and feeds
        # validator-passed entries through FlagGuard like any other
        # candidate.  Codex never reaches the platform directly.
        sidecar_cfg = cfg.get("codex_sidecar", {}) or {}
        self.codex_sidecar_enabled = bool(sidecar_cfg.get("enabled", False))

        paths = cfg.get("paths", {})
        self.state_dir = PROJECT / paths.get("state_dir", "state")
        self.artifacts_dir = PROJECT / paths.get("artifacts_dir", "artifacts")
        self.logs_dir = PROJECT / paths.get("logs_dir", "logs")
        self.locks_dir = PROJECT / paths.get("locks_dir", "state/locks")
        # Project root used by sidecar path policy: derive from
        # artifacts_dir so tests with a tmp project root keep working.
        self.project_root = self.artifacts_dir.parent
        for d in (self.state_dir, self.artifacts_dir, self.logs_dir, self.locks_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.state_path = self.state_dir / "ai_contest_state.json"
        self.snapshots_dir = self.state_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logger_obj or JsonlLogger(
            logs_dir=str(self.logs_dir),
            run_id=f"ai-contest-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        )
        self.agents = dict(agents or DEFAULT_AGENT_REGISTRY)

        self.runtime_capabilities = self._load_runtime_capabilities()
        self.auto_submit_categories = set(self.guard.auto_submit_categories)
        self._apply_runtime_capability_routing()

        self._stop_requested = False
        self._global_submit_disabled = False
        self.state = self._load_state()
        self._last_heartbeat = 0.0

    def _load_runtime_capabilities(self) -> Optional[dict[str, Any]]:
        path = PROJECT / "state" / "runtime_capabilities.json"
        if not path.exists():
            logger.warning(
                "runtime_capabilities.json missing; keeping configured auto_submit_categories"
            )
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to read runtime_capabilities.json: %s", exc)
            return None
        if not isinstance(payload, dict):
            logger.warning(
                "runtime_capabilities.json has invalid top-level type %s",
                type(payload).__name__,
            )
            return None
        return payload

    def _apply_runtime_capability_routing(self) -> None:
        """Verify each category in ``auto_submit_categories`` has its
        required runtime capabilities.

        Under AI identity there is no human reviewer to mediate a
        category that lost its solver stack at startup, so the
        supervisor refuses to start rather than silently demote the
        category to HUMAN_REVIEW.  The operator must either install the
        missing capability (re-run ``scripts/runtime_preflight.py``) or
        remove the category from ``submit.auto_submit_categories``
        before retrying.

        If the capability file is missing entirely (typical on
        rehearsal/local lab environments where preflight has not yet
        been generated) the routing fails open.
        """
        if not self.runtime_capabilities:
            self.guard.auto_submit_categories = set(self.auto_submit_categories)
            self.guard.submit_cfg["auto_submit_categories"] = sorted(self.auto_submit_categories)
            return

        caps = self.runtime_capabilities.get("capabilities") or {}
        if not isinstance(caps, dict):
            logger.warning(
                "runtime_capabilities.json missing capabilities map; keeping configured auto_submit_categories"
            )
            self.guard.auto_submit_categories = set(self.auto_submit_categories)
            self.guard.submit_cfg["auto_submit_categories"] = sorted(self.auto_submit_categories)
            return

        blocking: list[tuple[str, list[str]]] = []
        for category in sorted(self.auto_submit_categories):
            required = CATEGORY_REQUIRED_CAPS.get(category)
            if not required:
                continue
            available = [
                cap for cap in required
                if bool((caps.get(cap) or {}).get("available", False))
            ]
            if category == "crypto":
                satisfied = bool(available)
            else:
                satisfied = len(available) == len(required)
            if satisfied:
                continue
            missing = [cap for cap in required if cap not in available]
            self.logger.event(
                event_type="category_capability_missing",
                actor="supervisor",
                challenge_id="",
                category=category,
                message=(
                    f"{category} required capabilities missing: {missing} — "
                    f"AI-identity supervisor refuses to start"
                ),
                data={
                    "category": category,
                    "required_capabilities": list(required),
                    "missing_capabilities": missing,
                    "auto_submit_categories": sorted(self.auto_submit_categories),
                },
                redact=False,
            )
            blocking.append((category, missing))

        if blocking:
            details = "; ".join(f"{cat} missing {missing}" for cat, missing in blocking)
            raise RuntimeError(
                "runtime capabilities preflight failed for AI-identity profile: "
                f"{details}. Run scripts/runtime_preflight.py and install the "
                "listed capabilities, or remove the category from "
                "submit.auto_submit_categories."
            )

        # All required capabilities present.  Nothing demoted; guard
        # keeps the configured auto_submit_categories.
        self.guard.auto_submit_categories = set(self.auto_submit_categories)
        self.guard.submit_cfg["auto_submit_categories"] = sorted(self.auto_submit_categories)

    def _log_notification(
        self,
        notification: dict[str, Any],
        *,
        challenge_id: str = "",
        category: str = "",
    ) -> None:
        event = notification.get("event")
        if event in {"none", None}:
            return
        self.logger.event(
            event_type="notification",
            actor="supervisor",
            challenge_id=challenge_id,
            category=category,
            message=f"feishu {event}: sent={notification.get('sent', False)}",
            data={"notification": notification},
            redact=False,
        )

    # ---- state I/O -------------------------------------------------

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return {
                "schema": "ai-contest-v1",
                "game_id": self.game_id,
                "started_at": _utcnow_iso(),
                "challenges": {},
                "global_submit_disabled": False,
                "global_disable_reason": None,
            }
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            backup = self.state_path.with_suffix(self.state_path.suffix + ".corrupt")
            self.state_path.rename(backup)
            self.logger.event(
                event_type="state_corrupt_rotated",
                actor="supervisor",
                message=f"state file corrupt; rotated to {backup}",
                redact=False,
            )
            return self._load_state()
        payload.setdefault("challenges", {})
        payload.setdefault("global_submit_disabled", False)
        payload.setdefault("global_disable_reason", None)
        for cid, cstate in list(payload["challenges"].items()):
            if isinstance(cstate, dict):
                self._ensure_route_control_state(cstate, str(cid))
                self._ensure_route_control_action_state(cstate)
        self._global_submit_disabled = bool(payload["global_submit_disabled"])
        return payload

    def _save_state(self) -> None:
        for cid, cstate in list((self.state.get("challenges") or {}).items()):
            if isinstance(cstate, dict):
                self._ensure_route_control_state(cstate, str(cid))
                self._ensure_route_control_action_state(cstate)
        self.state["last_save"] = _utcnow_iso()
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.state_path)

    def _challenge_state(self, cid: str) -> dict:
        cstr = str(cid)
        if cstr not in self.state["challenges"]:
            self.state["challenges"][cstr] = _empty_challenge_state(cstr)
        cstate = self.state["challenges"][cstr]
        self._ensure_route_control_state(cstate, cstr)
        self._ensure_route_control_action_state(cstate)
        return cstate

    def _default_family_for_state(self, cstate: dict) -> str:
        category = (cstate.get("category") or "").lower()
        if category:
            return f"{category}.initial"
        return "misc.initial"

    def _ensure_route_control_state(self, cstate: dict, cid: str = "") -> dict:
        payload = cstate.get("route_control")
        if not isinstance(payload, dict):
            route = RouteState.new(current_family=self._default_family_for_state(cstate))
        else:
            route = RouteState.from_dict(payload)
            if not route.current_family:
                route.current_family = self._default_family_for_state(cstate)
        cstate["route_control"] = route.to_dict()
        return cstate["route_control"]

    def _ensure_route_control_action_state(self, cstate: dict) -> dict:
        defaults = _default_route_control_action_state()
        payload = cstate.get("route_control_action_state")
        if not isinstance(payload, dict):
            payload = {}
        for key, value in defaults.items():
            existing = payload.get(key)
            if not isinstance(existing, dict):
                payload[key] = dict(value)
            else:
                merged = dict(value)
                merged.update(existing)
                payload[key] = merged
        cstate["route_control_action_state"] = payload
        return payload

    def _load_route_state(self, cstate: dict) -> RouteState:
        self._ensure_route_control_state(cstate, str(cstate.get("challenge_id", "")))
        return RouteState.from_dict(cstate["route_control"])

    def _store_route_state(
        self,
        cstate: dict,
        route: RouteState,
        *,
        cid: str,
        category: str,
        reason: str,
    ) -> None:
        route = self._apply_route_control_actions(
            cstate,
            cid=cid,
            category=category,
            route=route,
        )
        cstate["route_control"] = route.to_dict()
        cstate["last_update"] = _utcnow_iso()
        self.logger.event(
            event_type="route_control_decision",
            actor="supervisor",
            challenge_id=cid,
            category=category,
            message=(
                f"decision={route.route_decision.value} "
                f"phase={route.route_phase.value} reason={reason}"
            ),
            data={
                "reason": reason,
                "route_control": cstate["route_control"],
            },
            redact=False,
        )

    def _write_route_control_action(
        self,
        cstate: dict,
        *,
        cid: str,
        category: str,
        route: RouteState,
        action: str,
    ) -> None:
        challenge_dir = self.artifacts_dir / "challenges" / cid
        challenge_dir.mkdir(parents=True, exist_ok=True)
        action_state = self._ensure_route_control_action_state(cstate)
        payload = {
            "challenge_id": cid,
            "category": category,
            "action": action,
            "current_family": route.current_family,
            "tried_families": [entry.to_dict() for entry in route.tried_families],
            "failure_type": route.failure_type.value if route.failure_type else None,
            "failure_signals": list(route.failure_signals),
            "route_decision": route.route_decision.value,
            "route_phase": route.route_phase.value,
            "public_search_status": route.public_search_status.value,
            "expert_review_status": route.expert_review_status.value,
            "persistent_lane_status": route.persistent_lane_status.value,
            "no_candidate_blockers": list(route.no_candidate_blockers),
            "persistent_lane": route.persistent_lane.to_dict(),
            "route_control": route.to_dict(),
            "timestamp": _utcnow_iso(),
        }
        if action == RouteDecision.SPAWN_PUBLIC_SEARCH.value:
            request_path = challenge_dir / "public_search_request.json"
            state_key = "public_search"
            next_status = "running"
        elif action == RouteDecision.SPAWN_EXPERT_REVIEW.value:
            request_path = challenge_dir / "expert_review_packet.json"
            state_key = "expert_review"
            next_status = "running"
        elif action == RouteDecision.SPAWN_PERSISTENT_LANE.value:
            request_path = challenge_dir / "persistent_lane_request.json"
            state_key = "persistent_lane"
            next_status = "active"
        else:
            return
        request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        action_state[state_key].update(
            {
                "status": next_status,
                "request_path": str(request_path),
                "requested_at": payload["timestamp"],
            }
        )
        self.logger.event(
            event_type="route_control_action",
            actor="supervisor",
            challenge_id=cid,
            category=category,
            message=f"action={action}",
            data={"action": action, "request_path": _display_path(request_path)},
            redact=False,
        )

    def _apply_route_control_actions(
        self,
        cstate: dict,
        *,
        cid: str,
        category: str,
        route: RouteState,
    ) -> RouteState:
        self._ensure_route_control_action_state(cstate)
        pending = list(route.pending_actions)
        if route.route_decision not in pending and route.route_decision is not RouteDecision.CONTINUE_ROUTE:
            pending.append(route.route_decision)
        for action in pending:
            if action is RouteDecision.CUT_ROUTE:
                self._apply_route_cut(cstate, route)
                route.pending_actions = [item for item in route.pending_actions if item is not action]
            elif action is RouteDecision.SWITCH_FAMILY:
                self._apply_family_switch(cstate, route)
                route.pending_actions = [item for item in route.pending_actions if item is not action]
            elif action in {
                RouteDecision.SPAWN_PUBLIC_SEARCH,
                RouteDecision.SPAWN_EXPERT_REVIEW,
                RouteDecision.SPAWN_PERSISTENT_LANE,
            }:
                if action is RouteDecision.SPAWN_PUBLIC_SEARCH:
                    route.public_search_status = PublicSearchStatus.RUNNING
                elif action is RouteDecision.SPAWN_EXPERT_REVIEW:
                    route.expert_review_status = ExpertReviewStatus.RUNNING
                elif action is RouteDecision.SPAWN_PERSISTENT_LANE:
                    route.persistent_lane_status = PersistentLaneStatus.ACTIVE
                    route.persistent_lane.status = PersistentLaneStatus.ACTIVE
                route.pending_actions = [item for item in route.pending_actions if item is not action]
                self._write_route_control_action(
                    cstate,
                    cid=cid,
                    category=category,
                    route=route,
                    action=action.value,
                )
        return route

    def _route_progress(
        self,
        cstate: dict,
        *,
        cid: str,
        category: str,
        progress: ProgressType = ProgressType.NO_PROGRESS,
        evidence_delta_score: Optional[int] = None,
        failure_type: Optional[FailureType] = None,
        failure_signals: Optional[list[str]] = None,
        consider_no_candidate: bool = False,
        reason: str,
    ) -> RouteState:
        route = evaluate_route(
            self._load_route_state(cstate),
            progress=progress,
            evidence_delta_score=evidence_delta_score,
            failure_type=failure_type,
            failure_signals=failure_signals,
            consider_no_candidate=consider_no_candidate,
        )
        self._store_route_state(cstate, route, cid=cid, category=category, reason=reason)
        return self._load_route_state(cstate)

    def _consume_route_gate_results(self, cstate: dict, *, cid: str, category: str) -> None:
        challenge_dir = self.artifacts_dir / "challenges" / cid
        route = self._load_route_state(cstate)
        action_state = self._ensure_route_control_action_state(cstate)
        changed = False

        if route.public_search_status in {PublicSearchStatus.REQUIRED, PublicSearchStatus.RUNNING}:
            result_path = self._route_result_path(
                challenge_dir,
                ("public_search_result.json", "public_search_ledger.json"),
            )
            if result_path is not None:
                result = self._load_json_object(result_path)
                if self._valid_public_search_result(result):
                    payload_status = str(result.get("status", ""))
                    complete = self._public_search_result_is_complete(result)
                    blocked_by_rules = payload_status == "blocked_by_rules"
                    gate_satisfied = complete or blocked_by_rules
                    route.next_family = _maybe_str(result.get("next_family")) or route.next_family
                    route.no_candidate_blockers = [
                        blocker
                        for blocker in route.no_candidate_blockers
                        if blocker != "public_search_required"
                    ]
                    for blocker in result.get("no_candidate_blockers") or []:
                        blocker = str(blocker)
                        if blocker and blocker not in route.no_candidate_blockers:
                            route.no_candidate_blockers.append(blocker)
                    if complete:
                        route.public_search_status = PublicSearchStatus.COMPLETE
                        gate_status = "complete"
                    elif blocked_by_rules:
                        route.public_search_status = PublicSearchStatus.BLOCKED_BY_RULES
                        gate_status = "blocked_by_rules"
                    else:
                        route.public_search_status = PublicSearchStatus.RUNNING
                        gate_status = "running"
                    if not gate_satisfied and "public_search_required" not in route.no_candidate_blockers:
                        route.no_candidate_blockers.append("public_search_required")
                    action_state["public_search"].update(
                        {
                            "status": gate_status,
                            "result_path": str(result_path),
                            "consumed_at": _utcnow_iso(),
                            "coverage": self._public_search_coverage(result),
                            "dispositions": self._public_search_dispositions(result),
                            "conclusion": str(result.get("conclusion", "")),
                            "next_family": _maybe_str(result.get("next_family")),
                            "no_candidate_blockers": [
                                str(item) for item in (result.get("no_candidate_blockers") or []) if item is not None
                            ],
                        }
                    )
                    changed = True
                elif result is not None:
                    action_state["public_search"].update(
                        {
                            "status": "running",
                            "result_path": str(result_path),
                            "last_error": "invalid_or_incomplete_result",
                        }
                    )

        if route.expert_review_status in {ExpertReviewStatus.REQUIRED, ExpertReviewStatus.RUNNING}:
            result_path = challenge_dir / "expert_review_result.json"
            if result_path.exists():
                result = self._load_json_object(result_path)
                if self._valid_expert_review_result(result):
                    route.expert_review_status = ExpertReviewStatus.COMPLETE
                    if "expert_review_required" in route.no_candidate_blockers:
                        route.no_candidate_blockers.remove("expert_review_required")
                    self._apply_expert_review_result(route, result, cstate)
                    action_state["expert_review"].update(
                        {
                            "status": "complete",
                            "result_path": str(result_path),
                            "consumed_at": _utcnow_iso(),
                            "verdict": str(result.get("verdict", "")),
                            "failure_class": str(result.get("failure_class", "")),
                            "continue_current_family": bool(result.get("continue_current_family", False)),
                            "next_families": [
                                str(item) for item in (result.get("next_families") or []) if item is not None
                            ],
                            "first_experiment": result.get("first_experiment"),
                            "stop_condition": str(result.get("stop_condition", "")),
                            "no_candidate_blockers": [
                                str(item) for item in (result.get("no_candidate_blockers") or []) if item is not None
                            ],
                        }
                    )
                    changed = True
                elif result is not None:
                    action_state["expert_review"].update(
                        {
                            "status": "running",
                            "result_path": str(result_path),
                            "last_error": "invalid_or_incomplete_result",
                        }
                    )

        lane_update_path = challenge_dir / "persistent_lane_update.json"
        if lane_update_path.exists():
            update_hash = self._artifact_content_hash(lane_update_path)
            if action_state["persistent_lane"].get("consumed_update_hash") == update_hash:
                # Same payload was already applied — re-running would
                # duplicate negative_evidence / append-style fields and
                # spuriously flip blockers around.
                pass
            else:
                result = self._load_json_object(lane_update_path)
                if self._valid_persistent_lane_update(result):
                    self._apply_persistent_lane_update(route, result, result_path=lane_update_path)
                    action_state["persistent_lane"].update(
                        {
                            "status": route.persistent_lane_status.value,
                            "result_path": str(lane_update_path),
                            "consumed_at": _utcnow_iso(),
                            "consumed_update_hash": update_hash,
                            "status_payload": str(result.get("status", "")),
                            "open_questions": [str(item) for item in (result.get("open_questions") or []) if item is not None],
                            "alternative_families": [str(item) for item in (result.get("alternative_families") or []) if item is not None],
                            "public_search_ledger": [
                                item for item in (result.get("public_search_ledger") or []) if isinstance(item, dict)
                            ],
                            "helper_evaluation": [str(item) for item in (result.get("helper_evaluation") or []) if item is not None],
                            "negative_evidence": [str(item) for item in (result.get("negative_evidence") or []) if item is not None],
                            "no_candidate_blockers": [str(item) for item in (result.get("no_candidate_blockers") or []) if item is not None],
                        }
                    )
                    changed = True
                elif result is not None:
                    action_state["persistent_lane"].update(
                        {
                            "result_path": str(lane_update_path),
                            "last_error": "invalid_or_incomplete_update",
                        }
                    )

        stop_report_path = challenge_dir / "persistent_lane_stop_report.json"
        if stop_report_path.exists():
            stop_report_hash = self._artifact_content_hash(stop_report_path)
            if action_state["persistent_lane"].get("consumed_stop_report_hash") == stop_report_hash:
                pass
            else:
                result = self._load_json_object(stop_report_path)
                if self._valid_persistent_lane_stop_report(result):
                    self._apply_persistent_lane_stop_report(route, result, result_path=stop_report_path)
                    action_state["persistent_lane"].update(
                        {
                            "status": route.persistent_lane_status.value,
                            "result_path": str(stop_report_path),
                            "consumed_at": _utcnow_iso(),
                            "consumed_stop_report_hash": stop_report_hash,
                            "stop_reason": str(result.get("stop_reason", "")),
                            "exhausted_families": [
                                str(item) for item in (result.get("exhausted_families") or []) if item is not None
                            ],
                            "remaining_blockers": [
                                str(item) for item in (result.get("remaining_blockers") or []) if item is not None
                            ],
                            "no_candidate_allowed": bool(result.get("no_candidate_allowed", False)),
                        }
                    )
                    changed = True
                elif result is not None:
                    action_state["persistent_lane"].update(
                        {
                            "result_path": str(stop_report_path),
                            "last_error": "invalid_or_incomplete_stop_report",
                        }
                    )

        if route.pending_actions:
            route = self._apply_route_control_actions(
                cstate,
                cid=cid,
                category=category,
                route=route,
            )
            changed = True

        if changed:
            cstate["route_control"] = route.to_dict()
            cstate["last_update"] = _utcnow_iso()
            self.logger.event(
                event_type="route_control_result_consumed",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message="route gate result consumed",
                data={
                    "route_control": cstate["route_control"],
                    "route_control_action_state": action_state,
                },
                redact=False,
            )

    def _route_gate_blocks_dispatch(self, cstate: dict, *, cid: str, category: str) -> bool:
        route = self._load_route_state(cstate)
        blocked_by: list[str] = []
        if route.public_search_status in {PublicSearchStatus.REQUIRED, PublicSearchStatus.RUNNING}:
            blocked_by.append("public_search")
        if route.expert_review_status in {ExpertReviewStatus.REQUIRED, ExpertReviewStatus.RUNNING}:
            blocked_by.append("expert_review")
        if not blocked_by:
            return False
        self.logger.event(
            event_type="route_control_gate_block",
            actor="supervisor",
            challenge_id=cid,
            category=category,
            message=f"blocked_by={','.join(blocked_by)}",
            data={"blocked_by": blocked_by, "route_control": route.to_dict()},
            redact=False,
        )
        return True

    def _route_result_path(self, challenge_dir: Path, names: tuple[str, ...]) -> Optional[Path]:
        for name in names:
            path = challenge_dir / name
            if path.exists():
                return path
        return None

    def _load_json_object(self, path: Path) -> Optional[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def _artifact_content_hash(self, path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    def _valid_public_search_result(self, payload: Optional[dict[str, Any]]) -> bool:
        if not isinstance(payload, dict):
            return False
        if str(payload.get("status", "")) not in {"complete", "blocked_by_rules"}:
            return False
        if not str(payload.get("coverage", "")):
            return False
        if not str(payload.get("conclusion", "")):
            return False
        if not isinstance(payload.get("no_candidate_blockers"), list):
            return False
        results = payload.get("results")
        if not isinstance(results, list):
            return False
        for item in results:
            if not isinstance(item, dict):
                return False
            if not str(item.get("query", "")):
                return False
            if not (str(item.get("url", "")) or str(item.get("source", ""))):
                return False
            if not str(item.get("summary", "")):
                return False
            if not str(item.get("disposition", "")):
                return False
        return True

    def _public_search_result_is_complete(self, payload: dict[str, Any]) -> bool:
        status = str(payload.get("status", ""))
        if status != "complete":
            return False
        dispositions = self._public_search_dispositions(payload)
        if not dispositions:
            return False
        if any(not disp or disp == "incomplete" for disp in dispositions):
            return False
        return True

    def _public_search_coverage(self, payload: dict[str, Any]) -> list[str]:
        coverage = payload.get("coverage")
        if isinstance(coverage, list):
            return [str(item) for item in coverage if item is not None]
        if coverage is None:
            return []
        return [str(coverage)]

    def _public_search_dispositions(self, payload: dict[str, Any]) -> list[str]:
        dispositions = payload.get("dispositions")
        if isinstance(dispositions, list):
            return [str(item) for item in dispositions if item is not None]
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        return [str(item.get("disposition", "")) for item in results if isinstance(item, dict)]

    def _valid_expert_review_result(self, payload: Optional[dict[str, Any]]) -> bool:
        if not isinstance(payload, dict):
            return False
        required = [
            "verdict",
            "failure_class",
            "continue_current_family",
            "next_families",
            "first_experiment",
            "stop_condition",
            "no_candidate_blockers",
        ]
        if any(key not in payload for key in required):
            return False
        if not isinstance(payload.get("continue_current_family"), bool):
            return False
        if not isinstance(payload.get("next_families"), list):
            return False
        if not isinstance(payload.get("first_experiment"), dict):
            return False
        if not isinstance(payload.get("no_candidate_blockers"), list):
            return False
        return bool(str(payload.get("verdict", ""))) and bool(str(payload.get("failure_class", "")))

    def _apply_expert_review_result(
        self,
        route: RouteState,
        result: dict[str, Any],
        cstate: dict,
    ) -> None:
        failure = result.get("failure_class")
        route.failure_type = FailureType(str(failure)) if str(failure) in {item.value for item in FailureType} else route.failure_type
        next_families = [str(item) for item in result.get("next_families", []) if item]
        verdict = str(result.get("verdict", ""))
        ends_current_family = (
            not result.get("continue_current_family")
            or verdict in {"cut_route", "switch_family"}
        )
        # will_switch must be derived from THIS expert result's
        # next_families, never from a stale route.next_family that an
        # earlier public-search ledger or persistent-lane update may
        # have left on the route.  cut_route is final regardless of
        # how many alternatives were proposed earlier.
        result_provides_next_family = bool(next_families)
        expert_cuts = verdict == "cut_route"
        will_switch = ends_current_family and result_provides_next_family and not expert_cuts
        will_cut = ends_current_family and not will_switch
        if will_switch:
            route.next_family = next_families[0]
        elif will_cut:
            # Drop any stale next_family suggestion the expert is
            # explicitly overriding; otherwise a later evaluate_route
            # tick would interpret it as a viable switch target.
            route.next_family = None
            # Drop any stale SWITCH_FAMILY queued by an earlier tick;
            # the expert's cut is the canonical decision now.
            route.pending_actions = [
                action
                for action in route.pending_actions
                if action is not RouteDecision.SWITCH_FAMILY
            ]
            action_state = self._ensure_route_control_action_state(cstate)
            action_state["family_switch"].pop("pending_first_experiment", None)
        if ends_current_family:
            route.route_phase = RoutePhase.CUT
            route.route_decision = RouteDecision.SWITCH_FAMILY if will_switch else RouteDecision.CUT_ROUTE
            if route.route_decision not in route.pending_actions:
                route.pending_actions.append(route.route_decision)
        first_experiment = result.get("first_experiment")
        if isinstance(first_experiment, dict) and first_experiment:
            if will_switch:
                action_state = self._ensure_route_control_action_state(cstate)
                action_state["family_switch"]["pending_first_experiment"] = dict(first_experiment)
            elif not will_cut:
                entry = route.ensure_family_entry()
                entry.experiments.append(dict(first_experiment))
        for blocker in result.get("no_candidate_blockers", []):
            blocker = str(blocker)
            if blocker and blocker not in route.no_candidate_blockers:
                route.no_candidate_blockers.append(blocker)

    def _valid_persistent_lane_update(self, payload: Optional[dict[str, Any]]) -> bool:
        if not isinstance(payload, dict):
            return False
        status = str(payload.get("status", ""))
        if status not in {item.value for item in PersistentLaneStatus} - {PersistentLaneStatus.NOT_STARTED.value}:
            return False
        for key in (
            "open_questions",
            "alternative_families",
            "public_search_ledger",
            "helper_evaluation",
            "negative_evidence",
            "no_candidate_blockers",
        ):
            if not isinstance(payload.get(key), list):
                return False
        return True

    def _apply_persistent_lane_update(self, route: RouteState, result: dict[str, Any], *, result_path: Path) -> None:
        status = PersistentLaneStatus(str(result.get("status")))
        route.persistent_lane_status = status
        route.persistent_lane.status = status
        route.persistent_lane.open_questions = [str(item) for item in result.get("open_questions", []) if item]
        route.persistent_lane.alternative_families = [
            str(item) for item in result.get("alternative_families", []) if item
        ]
        route.persistent_lane.public_search_ledger = [
            SearchLedgerEntry.from_dict(item)
            for item in result.get("public_search_ledger", [])
            if isinstance(item, dict)
        ]
        route.persistent_lane.helper_evaluation = [
            str(item) for item in result.get("helper_evaluation", []) if item
        ]
        route.persistent_lane.negative_evidence = [
            str(item) for item in result.get("negative_evidence", []) if item
        ]
        route.persistent_lane.no_candidate_blockers = [
            str(item) for item in result.get("no_candidate_blockers", []) if item
        ]
        if status in {PersistentLaneStatus.COMPLETE, PersistentLaneStatus.SUSPENDED, PersistentLaneStatus.STALE, PersistentLaneStatus.STOPPED}:
            route.persistent_lane.stop_report_path = str(result_path)
        if not route.persistent_lane.no_candidate_blockers:
            route.no_candidate_blockers = [
                blocker
                for blocker in route.no_candidate_blockers
                if blocker
                not in {
                    "persistent_lane_active",
                    "persistent_lane_blockers",
                    "persistent_lane_stop_report_required",
                    "persistent_lane_required",
                }
            ]

    def _valid_persistent_lane_stop_report(self, payload: Optional[dict[str, Any]]) -> bool:
        if not isinstance(payload, dict):
            return False
        if str(payload.get("status", "")) not in {
            PersistentLaneStatus.COMPLETE.value,
            PersistentLaneStatus.SUSPENDED.value,
            PersistentLaneStatus.STOPPED.value,
        }:
            return False
        if not str(payload.get("stop_reason", "")):
            return False
        if not isinstance(payload.get("exhausted_families"), list):
            return False
        if not isinstance(payload.get("remaining_blockers"), list):
            return False
        if payload.get("no_candidate_allowed") is not True:
            return False
        return True

    def _apply_persistent_lane_stop_report(self, route: RouteState, result: dict[str, Any], *, result_path: Path) -> None:
        status = PersistentLaneStatus(str(result.get("status")))
        route.persistent_lane_status = status
        route.persistent_lane.status = status
        route.persistent_lane.stop_report_path = str(result_path)
        route.persistent_lane.no_candidate_blockers = [
            str(item) for item in result.get("remaining_blockers", []) if item
        ]
        exhausted_families = [str(item) for item in result.get("exhausted_families", []) if item]
        route.persistent_lane.alternative_families = exhausted_families
        route.persistent_lane.negative_evidence.append(str(result.get("stop_reason", "")))
        if not route.persistent_lane.no_candidate_blockers:
            route.no_candidate_blockers = [
                blocker
                for blocker in route.no_candidate_blockers
                if blocker
                not in {
                    "persistent_lane_active",
                    "persistent_lane_blockers",
                    "persistent_lane_stop_report_required",
                    "persistent_lane_required",
                }
            ]

    def _apply_route_cut(self, cstate: dict, route: RouteState) -> None:
        entry = route.ensure_family_entry()
        entry.status = "cut"
        if route.failure_type is not None:
            entry.failure_type = entry.failure_type or route.failure_type.value
            entry.cut_reason = entry.cut_reason or route.failure_type.value
        else:
            entry.cut_reason = entry.cut_reason or "cut_route"
        if route.failure_signals and not entry.failure_signals:
            entry.failure_signals = list(route.failure_signals)
        if entry.ended_at_cycle is None:
            entry.ended_at_cycle = route.route_cycle
        if route.route_phase is RoutePhase.ACTIVE:
            route.route_phase = RoutePhase.CUT
        self.logger.event(
            event_type="route_control_action",
            actor="supervisor",
            challenge_id=str(cstate.get("challenge_id", "")),
            category=cstate.get("category", ""),
            message=f"action={RouteDecision.CUT_ROUTE.value}",
            data={
                "action": RouteDecision.CUT_ROUTE.value,
                "current_family": route.current_family,
                "cut_reason": entry.cut_reason,
            },
            redact=False,
        )

    def _apply_family_switch(self, cstate: dict, route: RouteState) -> None:
        target = route.next_family
        if not target or target == route.current_family:
            return
        action_state = self._ensure_route_control_action_state(cstate)
        previous = route.current_family
        family_entry = route.ensure_family_entry(reason=f"switched from {previous}")
        family_entry.status = "cut"
        # Preserve any audit data already written by an earlier
        # CUT_ROUTE pass or evaluate_route's own cut handler — the
        # original failure is more useful than a generic
        # "switch_family" stamp.  Only fill empty slots.
        if not family_entry.cut_reason:
            if route.failure_type is not None:
                family_entry.cut_reason = route.failure_type.value
            else:
                family_entry.cut_reason = "switch_family"
        if not family_entry.failure_type and route.failure_type is not None:
            family_entry.failure_type = route.failure_type.value
        if not family_entry.failure_signals and route.failure_signals:
            family_entry.failure_signals = list(route.failure_signals)
        if family_entry.ended_at_cycle is None:
            family_entry.ended_at_cycle = route.route_cycle
        route.current_family = target
        route.next_family = None
        route.route_phase = RoutePhase.ACTIVE
        route.route_decision = RouteDecision.CONTINUE_ROUTE
        route.failure_type = None
        route.failure_signals = []
        route.evidence_delta_score = 0
        route.same_family_no_delta_count = 0
        route.trivial_root_count = 0
        route.family_switch_done = True
        new_entry = route.ensure_family_entry(reason=f"switched from {previous}")
        pending_first_experiment = action_state["family_switch"].pop(
            "pending_first_experiment", None
        )
        if isinstance(pending_first_experiment, dict) and pending_first_experiment:
            new_entry.experiments.append(dict(pending_first_experiment))
        action_state["family_switch"].update(
            {
                "status": "complete",
                "from_family": previous,
                "to_family": target,
                "switched_at": _utcnow_iso(),
            }
        )
        self.logger.event(
            event_type="route_control_action",
            actor="supervisor",
            challenge_id=str(cstate.get("challenge_id", "")),
            category=cstate.get("category", ""),
            message=f"action={RouteDecision.SWITCH_FAMILY.value}",
            data={
                "action": RouteDecision.SWITCH_FAMILY.value,
                "from_family": previous,
                "to_family": target,
            },
            redact=False,
        )

    def _candidate_queue_empty(self, cid: str) -> bool:
        candidates_path = self.artifacts_dir / "challenges" / cid / "codex_candidates.json"
        if not candidates_path.exists():
            return False
        try:
            entries = json.loads(candidates_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        return entries == []

    def _has_candidate_queue_file(self, cid: str) -> bool:
        return (self.artifacts_dir / "challenges" / cid / "codex_candidates.json").exists()

    def _normalize_family_switch_proof(self, cstate: dict, route: RouteState) -> None:
        action_state = self._ensure_route_control_action_state(cstate)
        family_switch = action_state.get("family_switch") or {}
        real_switch_done = (
            family_switch.get("status") == "complete"
            and bool(family_switch.get("from_family"))
            and bool(family_switch.get("to_family"))
            and bool(family_switch.get("switched_at"))
        )
        if real_switch_done:
            route.family_switch_done = True
            return
        if route.family_switch_justified_impossible and route.family_switch_impossible_reason:
            route.family_switch_done = False
            return
        route.family_switch_done = False

    def _handle_no_candidate(
        self,
        cstate: dict,
        *,
        cid: str,
        category: str,
        reason: str,
    ) -> None:
        route = self._load_route_state(cstate)
        route.local_baseline_done = True
        route.short_codex_done = True
        if self._has_candidate_queue_file(cid):
            route.candidate_queue_empty = self._candidate_queue_empty(cid)
        self._normalize_family_switch_proof(cstate, route)
        if route.failure_type is None:
            route.failure_type = FailureType.EVIDENCE_INSUFFICIENT
        updated = evaluate_route(
            route,
            progress=ProgressType.NO_PROGRESS,
            failure_type=route.failure_type,
            failure_signals=[reason],
            consider_no_candidate=True,
        )
        self._store_route_state(cstate, updated, cid=cid, category=category, reason=reason)
        updated = self._load_route_state(cstate)
        if updated.route_decision is RouteDecision.ALLOW_NO_CANDIDATE:
            cstate["state"] = CHALLENGE_STATE_NO_CANDIDATE
        else:
            cstate["state"] = CHALLENGE_STATE_NO_AGENT
        cstate["last_update"] = _utcnow_iso()

    # ---- lifecycle -------------------------------------------------

    def request_stop(self) -> None:
        self._stop_requested = True

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: self.request_stop())
            except (ValueError, OSError):
                # No signal handling in subthreads / certain test envs
                pass

    def healthcheck(self) -> bool:
        """Single startup probe.  Logs and returns False on any failure
        so the operator (or the rehearsal harness) can decide whether
        to enter the contest loop or fall back to student identity."""
        try:
            self.adapter.login()
            profile = self.adapter.profile()
            team = self.adapter.current_team()
            game = self.adapter.game(self.game_id)
            details = self.adapter.game_details(self.game_id)
        except Exception as exc:  # noqa: BLE001
            self.logger.event(
                event_type="healthcheck_fail",
                actor="supervisor",
                message=f"{type(exc).__name__}: {exc}",
                redact=False,
            )
            return False
        ch_count = len(normalize_challenges(details))
        self.logger.event(
            event_type="healthcheck_ok",
            actor="supervisor",
            message=(
                f"profile.user={profile.get('userName','?')} "
                f"team.id={team.get('id','?')} game.id={game.get('id','?')} "
                f"challenges={ch_count}"
            ),
            data={
                "profile": {"userName": profile.get("userName")},
                "team_id": team.get("id"),
                "challenges_count": ch_count,
            },
            redact=False,
        )
        return True

    def heartbeat(self, *, force: bool = False) -> None:
        now = self._clock()
        if not force and now - self._last_heartbeat < self.heartbeat_interval_s:
            return
        self._last_heartbeat = now
        counts = Counter(c["state"] for c in self.state["challenges"].values())
        self.logger.event(
            event_type="heartbeat",
            actor="supervisor",
            message=(
                f"running challenges={len(self.state['challenges'])} "
                f"global_submit_disabled={self._global_submit_disabled} "
                f"states={dict(counts)}"
            ),
            data={
                "challenges_count": len(self.state["challenges"]),
                "global_submit_disabled": self._global_submit_disabled,
                "challenge_state_counts": dict(counts),
            },
            redact=False,
        )

    # ---- sync ------------------------------------------------------

    def sync_challenges(self) -> list[dict]:
        """Pulls /api/game/{id}/details, registers any new challenge
        ids in local state, returns the flattened challenge list."""
        details = self.adapter.game_details(self.game_id)
        challenges = normalize_challenges(details)
        for ch in challenges:
            cid = str(ch.get("id"))
            if not cid:
                continue
            cstate = self._challenge_state(cid)
            if cstate["state"] == CHALLENGE_STATE_DISCOVERED:
                cstate.update(
                    {
                        "title": ch.get("title", "")[:120],
                        "category": (ch.get("category") or "").lower(),
                        "type": ch.get("type", ""),
                        "last_update": _utcnow_iso(),
                    }
                )
                route = RouteState.from_dict(cstate["route_control"])
                if route.current_family == "misc.initial":
                    route.current_family = self._default_family_for_state(cstate)
                    cstate["route_control"] = route.to_dict()
                self.logger.event(
                    event_type="challenge_seen",
                    actor="supervisor",
                    challenge_id=cid,
                    category=cstate["category"],
                    message=f"new challenge {cstate['title']!r}",
                    redact=False,
                )
        self._save_state()
        return challenges

    # ---- per-challenge step ---------------------------------------

    def step_challenge(self, ch_meta: dict) -> None:
        cid = str(ch_meta.get("id"))
        cstate = self._challenge_state(cid)
        if cstate["state"] in CHALLENGE_TERMINAL_STATES:
            return
        if self._global_submit_disabled and cstate["state"] != CHALLENGE_STATE_PENDING:
            return

        category = cstate.get("category") or (ch_meta.get("category") or "").lower()

        # Pending state is a hard gate: a previous submit is still in
        # flight on the platform.  Per the AI-identity rule we never
        # produce a second candidate while pending.  Poll the previous
        # submit's status; only when it terminalises do we move on.
        if cstate["state"] == CHALLENGE_STATE_PENDING:
            self._resolve_pending(cstate, cid, category)
            return

        # 1. fetch detail (idempotent — overwrites context)
        detail = self.adapter.challenge_detail(self.game_id, int(cid))
        cstate["state"] = max_state(cstate["state"], CHALLENGE_STATE_DETAIL_FETCHED)
        cstate["last_update"] = _utcnow_iso()

        # 2. download attachment if static-style
        attachment_url = (
            detail.get("context", {}).get("url")
            or detail.get("attachmentUrl")
            or ""
        )
        if attachment_url and not cstate["attachment_paths"]:
            try:
                target = self.adapter.download_attachment(
                    attachment_url,
                    self.artifacts_dir / "challenges" / cid,
                )
                cstate["attachment_paths"].append(str(target))
                cstate["state"] = max_state(cstate["state"], CHALLENGE_STATE_DOWNLOADED)
                self.logger.event(
                    event_type="attachment_downloaded",
                    actor="supervisor",
                    challenge_id=cid,
                    category=category,
                    message=f"saved {target.name}",
                    redact=False,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.event(
                    event_type="attachment_error",
                    actor="supervisor",
                    challenge_id=cid,
                    category=category,
                    message=f"{type(exc).__name__}: {exc}",
                    redact=False,
                )

        self._consume_route_gate_results(cstate, cid=cid, category=category)
        if self._route_gate_blocks_dispatch(cstate, cid=cid, category=category):
            self._save_state()
            return

        # 3a. (optional) Codex sidecar candidate ingestion.  Per Codex
        # review §1, the sidecar validator + path policy are wired in
        # but completely skipped unless cfg.codex_sidecar.enabled is
        # True.  When enabled, the supervisor reads
        # artifacts/challenges/<id>/codex_candidates.json, validates
        # each entry, and prefers the first validator-passed entry
        # over the built-in agent's output.  Validator-rejected
        # entries emit `codex_candidate_rejected`.
        codex_candidate = None
        if self.codex_sidecar_enabled:
            codex_candidate = self._ingest_codex_candidate(cid, category)

        # 3b. dispatch built-in agent if category enabled (and no
        # Codex candidate took precedence).
        candidate = codex_candidate
        if candidate is None:
            if category not in self.enabled_categories:
                self._handle_no_candidate(
                    cstate,
                    cid=cid,
                    category=category,
                    reason=f"category {category} not enabled for built-in agent",
                )
                self._save_state()
                return
            agent = self.agents.get(category) or self.agents.get("misc")
            if agent is None:
                self._handle_no_candidate(
                    cstate,
                    cid=cid,
                    category=category,
                    reason=f"no agent registered for category {category}",
                )
                self._save_state()
                return

            chal = Challenge(
                id=cid,
                title=cstate.get("title") or detail.get("title", ""),
                category=category,
                description=(detail.get("content") or "")[:1000],
                attachments=list(cstate["attachment_paths"]),
            )
            candidate = agent(chal)
        if candidate is None:
            if cstate["state"] not in CHALLENGE_TERMINAL_STATES:
                self._handle_no_candidate(
                    cstate,
                    cid=cid,
                    category=category,
                    reason="agent and sidecar produced no candidate",
                )
            self._save_state()
            return

        # 4. dedupe by hash
        cand_hash = _flag_hash(candidate.flag)
        if cand_hash in cstate["submitted_flag_hashes"]:
            self.logger.event(
                event_type="duplicate_candidate_skipped",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message="candidate flag hash already submitted",
                redact=False,
            )
            self._route_progress(
                cstate,
                cid=cid,
                category=category,
                progress=ProgressType.NO_PROGRESS,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT,
                failure_signals=["duplicate candidate skipped"],
                reason="duplicate_candidate_skipped",
            )
            self._save_state()
            return

        # 5. guard.  Sanitize the decision before logging so the
        # plaintext flag never lands in JSONL (Codex review §1).
        decision = self.guard.decide(candidate)
        self.logger.event(
            event_type="guard_decision",
            actor="supervisor",
            challenge_id=cid,
            category=category,
            message=f"action={decision.action.value} reason={decision.reason}",
            data=_sanitize_decision_for_log(decision.to_dict()),
            confidence=decision.score,
            redact=False,
        )
        notification = notify_decision(self.feishu_cfg, decision)
        self._log_notification(notification, challenge_id=cid, category=category)
        if decision.action is not Decision.AUTO_SUBMIT:
            self._route_progress(
                cstate,
                cid=cid,
                category=category,
                progress=ProgressType.NO_PROGRESS,
                failure_type=FailureType.EVIDENCE_INSUFFICIENT,
                failure_signals=[f"guard held candidate: {decision.action.value}"],
                reason=f"guard_{decision.action.value}",
            )
            self._save_state()
            return

        self._route_progress(
            cstate,
            cid=cid,
            category=category,
            progress=ProgressType.CHALLENGE_PROGRESS,
            evidence_delta_score=1,
            failure_signals=["candidate passed guard"],
            reason="candidate_passed_guard",
        )

        # 6. submit — and stash the minimum context needed to call
        # state_store.record_outcome_for_pending() if/when this submit
        # terminalises later via the pending-resolve path.  We store
        # the redacted form + hash, never plaintext, so
        # state/ai_contest_state.json doesn't carry plaintext flags
        # (Codex review §2 hygiene fix).
        cstate["submitted_flag_hashes"].append(cand_hash)
        cstate["pending_record_payload"] = {
            "flag_hash": cand_hash,
            "flag_redacted": _redact_flag_for_state(candidate.flag),
            "force": bool(decision.force_submit),
        }
        cstate["state"] = CHALLENGE_STATE_PENDING
        self._save_state()
        try:
            outcome = self.adapter.submit_flag_for_game(
                game_id=self.game_id,
                challenge_id=int(cid),
                flag=candidate.flag,
                poll_timeout_s=self.poll_timeout_s,
                poll_interval_s=self.poll_interval_s,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.event(
                event_type="submit_error",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message=f"{type(exc).__name__}: {exc}",
                redact=False,
            )
            self._save_state()
            return

        cstate["last_submit_id"] = outcome.submit_id
        self.logger.event(
            event_type="submit_outcome",
            actor="supervisor",
            challenge_id=cid,
            category=category,
            message=f"status={outcome.status} kind={outcome.kind}",
            data={"status": outcome.status, "kind": outcome.kind},
            redact=False,
        )

        # 7. record into FlagGuard for rate limiting / freeze counters
        state_update = self.guard.record_outcome(
            candidate, decision, correct=outcome.correct, platform_response=outcome.status
        )
        if outcome.kind in {"accepted", "wrong"}:
            state_update = {
                **state_update,
                "accepted": outcome.kind == "accepted",
                "platform_response": outcome.status,
            }
            outcome_notification = notify_submit_outcome(
                self.feishu_cfg,
                decision=decision,
                state_update=state_update,
                max_wrong=self.guard.max_wrong_per_challenge,
                log_hint=str(self.logger.path),
            )
            self._log_notification(outcome_notification, challenge_id=cid, category=category)

        if outcome.terminal:
            cstate.pop("pending_record_payload", None)

        # 8. translate outcome into terminal challenge state
        self._apply_outcome_to_state(cstate, outcome, cid, category, cand_hash=cand_hash)
        self._save_state()

    def _ingest_codex_candidate(self, cid: str, category: str) -> Optional[FlagCandidate]:
        """Read ``artifacts/challenges/<cid>/codex_candidates.json``,
        validate each entry, and return a FlagCandidate built from the
        first one that passes.  Drops entries that fail validation
        with a ``codex_candidate_rejected`` event.

        The path itself is checked through ``is_safe_artifact_path``
        to defeat symlink escape; the JSON content is checked through
        ``validate_codex_candidate`` against the strict schema in
        ``runbooks/codex_sidecar.md``.
        """
        artifact_dir = self.artifacts_dir / "challenges" / cid
        candidates_path = artifact_dir / "codex_candidates.json"
        if not is_safe_artifact_path(candidates_path, self.project_root):
            self.logger.event(
                event_type="codex_candidate_rejected",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message="codex_candidates.json path escaped sandbox",
                redact=False,
            )
            return None
        if not candidates_path.exists():
            return None
        try:
            entries = json.loads(candidates_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.event(
                event_type="codex_candidate_rejected",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message=f"codex_candidates.json unreadable: {exc}",
                redact=False,
            )
            return None
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            self.logger.event(
                event_type="codex_candidate_rejected",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message=f"codex_candidates.json top-level must be array or object, got {type(entries).__name__}",
                redact=False,
            )
            return None

        for entry in entries:
            errs = validate_codex_candidate(entry, expected_challenge_id=cid)
            if errs:
                self.logger.event(
                    event_type="codex_candidate_rejected",
                    actor="supervisor",
                    challenge_id=cid,
                    category=category,
                    message=f"validator rejected: {errs[:3]}",
                    data={"errors": errs, "reason": "schema"},
                    redact=False,
                )
                continue
            confidence = entry.get("confidence")
            evidence_paths = entry.get("evidence_paths") or []
            # Per Codex review §3 — only confidence=high candidates can
            # short-circuit the internal agent.  Lower confidence is
            # advisory: the supervisor records the note but still lets
            # the built-in agent run, so a single low-confidence Codex
            # output cannot indefinitely starve a category.
            if confidence != "high":
                self.logger.event(
                    event_type="codex_candidate_advisory_only",
                    actor="supervisor",
                    challenge_id=cid,
                    category=category,
                    message=f"confidence={confidence} (advisory; internal agent will run)",
                    data={
                        "confidence": confidence,
                        "evidence_paths_count": len(evidence_paths),
                        "notes_len": len(entry.get("notes") or ""),
                    },
                    redact=False,
                )
                continue
            # Per Codex review §2 — schema validity is not enough.  The
            # supervisor must also confirm each declared evidence file
            # actually exists inside the sandbox before treating Codex
            # output as a submit candidate.  A schema-valid path that
            # points at a non-existent file would otherwise let a
            # drifted / hallucinated Codex output trigger auto-submit
            # on a candidate the operator can't trace back to a tool
            # output.
            evidence_problems: list[str] = []
            for ep in evidence_paths:
                if not isinstance(ep, str):
                    evidence_problems.append(f"{ep!r}: not_a_string")
                    continue
                target = self.project_root / ep
                if not is_safe_artifact_path(target, self.project_root):
                    evidence_problems.append(f"{ep}: out_of_sandbox")
                    continue
                if not target.exists():
                    evidence_problems.append(f"{ep}: not_found")
                    continue
                if not target.is_file():
                    evidence_problems.append(f"{ep}: not_a_regular_file")
            if evidence_problems:
                self.logger.event(
                    event_type="codex_candidate_rejected",
                    actor="supervisor",
                    challenge_id=cid,
                    category=category,
                    message=(
                        f"evidence files missing or invalid: {evidence_problems[:3]}"
                    ),
                    data={
                        "reason": "evidence_missing",
                        "evidence_problems": evidence_problems,
                    },
                    redact=False,
                )
                continue
            # Schema valid + confidence=high + every declared evidence
            # file is a real regular file inside the sandbox.  Log
            # only metadata (no notes excerpt — notes can contain the
            # flag itself; Codex review §1) and feed the candidate
            # into FlagGuard like any other.
            self.logger.event(
                event_type="codex_candidate_received",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message=f"confidence={confidence}",
                data={
                    "confidence": confidence,
                    "evidence_paths_count": len(evidence_paths),
                    "notes_len": len(entry.get("notes") or ""),
                },
                redact=False,
            )
            # Map confidence=high onto FlagGuard inputs that satisfy
            # the auto-submit score floor.  Even so, the candidate
            # still has to clear rate-limit, kill switch, and category
            # whitelist — Codex output never bypasses guard.
            flag = entry["candidate"]
            return FlagCandidate(
                challenge_id=cid,
                flag=flag,
                category=category,
                evidence_count=4,
                extraction_confidence=1.0,
                agent_votes=[flag] * 3,
                risk="normal",
            )
        return None

    def _resolve_pending(self, cstate: dict, cid: str, category: str) -> None:
        """Single-poll the last submit's status; if still pending,
        leave state unchanged so the next tick polls again."""
        sid = cstate.get("last_submit_id")
        if sid is None:
            # Defensive: shouldn't happen, but if it does, mark as
            # platform anomaly so the supervisor stops looping forever.
            cstate["state"] = CHALLENGE_STATE_NOTFOUND
            cstate["freeze_reason"] = "pending_without_submit_id"
            cstate["last_update"] = _utcnow_iso()
            self._save_state()
            return
        try:
            outcome = self.adapter.poll_submission_status(
                self.game_id, int(cid), int(sid),
                timeout_s=0.0, interval_s=0.0,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger.event(
                event_type="pending_poll_error",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message=f"{type(exc).__name__}: {exc}",
                redact=False,
            )
            return
        if not outcome.terminal:
            self.logger.event(
                event_type="pending_still",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message=f"submit_id={sid} status={outcome.status}",
                redact=False,
            )
            return
        # Terminal — apply transition AND sync guard's wrong_count /
        # frozen via state_store.record_submit.  This is the missing
        # piece flagged by Codex review: the original submit path
        # only PROVISIONALLY records via the synchronous outcome; if
        # the platform was still pending then, guard never saw the
        # final correct=True/False.
        cand_hash = (
            cstate["submitted_flag_hashes"][-1]
            if cstate.get("submitted_flag_hashes")
            else None
        )
        payload = cstate.get("pending_record_payload") or {}
        # Tolerate both the legacy {"flag": "<plaintext>"} shape (older
        # state files written before the hygiene fix) and the new
        # {"flag_redacted", "flag_hash"} shape.
        flag_redacted = payload.get("flag_redacted") or _redact_flag_for_state(
            payload.get("flag", "")
        )
        if flag_redacted:
            try:
                state_update = self.guard.state_store.record_outcome_for_pending(
                    challenge_id=cid,
                    flag_redacted=flag_redacted,
                    correct=outcome.correct,
                    max_wrong=self.guard.max_wrong_per_challenge,
                    force=bool(payload.get("force", False)),
                    platform_response=outcome.status,
                )
                decision_for_notification = GuardDecision(
                    action=Decision.AUTO_SUBMIT,
                    flag=flag_redacted,
                    challenge_id=cid,
                    category=category,
                )
                if outcome.kind in {"accepted", "wrong"}:
                    state_update = {
                        **state_update,
                        "accepted": outcome.kind == "accepted",
                        "platform_response": outcome.status,
                    }
                    outcome_notification = notify_submit_outcome(
                        self.feishu_cfg,
                        decision=decision_for_notification,
                        state_update=state_update,
                        max_wrong=self.guard.max_wrong_per_challenge,
                        log_hint=str(self.logger.path),
                    )
                    self._log_notification(
                        outcome_notification,
                        challenge_id=cid,
                        category=category,
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.event(
                    event_type="pending_record_error",
                    actor="supervisor",
                    challenge_id=cid,
                    category=category,
                    message=f"{type(exc).__name__}: {exc}",
                    redact=False,
                )
        cstate.pop("pending_record_payload", None)
        self.logger.event(
            event_type="pending_terminalised",
            actor="supervisor",
            challenge_id=cid,
            category=category,
            message=f"status={outcome.status} kind={outcome.kind}",
            redact=False,
        )
        self._apply_outcome_to_state(cstate, outcome, cid, category, cand_hash=cand_hash)
        self._save_state()

    def _apply_outcome_to_state(
        self,
        cstate: dict,
        outcome,
        cid: str,
        category: str,
        *,
        cand_hash,
    ) -> None:
        if outcome.kind == "accepted":
            cstate["state"] = CHALLENGE_STATE_ACCEPTED
            cstate["accepted_flag_hash"] = cand_hash
        elif outcome.kind == "wrong":
            cstate["wrong_count"] = int(cstate.get("wrong_count", 0)) + 1
            cstate["state"] = CHALLENGE_STATE_WRONG_FROZEN
            cstate["freeze_reason"] = "wrong_answer"
        elif outcome.kind == "cheat":
            cstate["state"] = CHALLENGE_STATE_CHEAT_FROZEN
            cstate["freeze_reason"] = "cheat_detected"
            self._global_submit_disabled = True
            self.state["global_submit_disabled"] = True
            self.state["global_disable_reason"] = "CheatDetected"
            self.logger.event(
                event_type="global_submit_disabled",
                actor="supervisor",
                challenge_id=cid,
                category=category,
                message="CheatDetected → all further submits disabled",
                redact=False,
            )
            notification = {
                "event": "kill_switch",
                **notify_kill_switch(
                    self.feishu_cfg,
                    activated=True,
                    reason="CheatDetected",
                ),
            }
            self._log_notification(notification, challenge_id=cid, category=category)
        elif outcome.kind == "not_found":
            cstate["state"] = CHALLENGE_STATE_NOTFOUND
            cstate["freeze_reason"] = "platform_not_found"
        elif outcome.kind == "pending":
            # Stay pending; future ticks call _resolve_pending().
            pass
        cstate["last_update"] = _utcnow_iso()

    # ---- main loop -------------------------------------------------

    def run_one_tick(self) -> None:
        try:
            challenges = self.sync_challenges()
        except Exception as exc:  # noqa: BLE001
            self.logger.event(
                event_type="sync_error",
                actor="supervisor",
                message=f"{type(exc).__name__}: {exc}",
                redact=False,
            )
            return
        for ch_meta in challenges:
            if self._stop_requested:
                break
            try:
                self.step_challenge(ch_meta)
            except Exception as exc:  # noqa: BLE001
                self.logger.event(
                    event_type="step_error",
                    actor="supervisor",
                    challenge_id=str(ch_meta.get("id")),
                    message=f"{type(exc).__name__}: {exc}",
                    redact=False,
                )
        self.heartbeat()

    def run(self) -> int:
        self.install_signal_handlers()
        self.heartbeat(force=True)
        deadline = self._clock() + self.global_timeout_s
        while not self._stop_requested:
            self.run_one_tick()
            if self._clock() >= deadline:
                self.logger.event(
                    event_type="global_timeout_reached",
                    actor="supervisor",
                    message=f"global_run_timeout_s={self.global_timeout_s} reached",
                    redact=False,
                )
                break
            self._sleep(self.loop_interval_s)
        self.heartbeat(force=True)
        self._save_state()
        return 0


def normalize_challenges(details: dict) -> list[dict]:
    """Flatten ``GameDetailModel.challenges`` into a list.

    Per the official GZCTF OpenAPI, ``challenges`` is a
    ``Dictionary<string, ChallengeInfo[]>`` keyed by category, e.g.
    ``{"Misc": [<ch>, ...], "Web": [<ch>, ...]}``.  Older forks (and
    our local mock) may emit it as a flat list.  This helper accepts
    both shapes and back-fills ``category`` from the dict key when the
    entry itself doesn't carry one.
    """
    raw = details.get("challenges") if isinstance(details, dict) else None
    if raw is None:
        return []
    if isinstance(raw, dict):
        flat: list[dict] = []
        for cat, ch_list in raw.items():
            if not isinstance(ch_list, list):
                continue
            for ch in ch_list:
                if not isinstance(ch, dict):
                    continue
                if not ch.get("category"):
                    ch = dict(ch)
                    ch["category"] = cat
                flat.append(ch)
        return flat
    if isinstance(raw, list):
        return [ch for ch in raw if isinstance(ch, dict)]
    return []


def max_state(current: str, candidate: str) -> str:
    """Monotonic state ordering: never regress past terminal."""
    rank = {
        CHALLENGE_STATE_DISCOVERED: 0,
        CHALLENGE_STATE_DETAIL_FETCHED: 1,
        CHALLENGE_STATE_DOWNLOADED: 2,
        CHALLENGE_STATE_NO_AGENT: 3,
        CHALLENGE_STATE_NO_CANDIDATE: 3,
        CHALLENGE_STATE_PENDING: 4,
        CHALLENGE_STATE_NOTFOUND: 5,
        CHALLENGE_STATE_ACCEPTED: 6,
        CHALLENGE_STATE_WRONG_FROZEN: 6,
        CHALLENGE_STATE_CHEAT_FROZEN: 6,
    }
    if rank.get(candidate, 0) > rank.get(current, 0):
        return candidate
    return current


# ---- CLI -----------------------------------------------------------


def _build_adapter(cfg: dict) -> GZCTFAdapter:
    gz = cfg["gzctf"]
    auth_mode = gz.get("auth_mode", "auto")
    username = os.environ.get(gz.get("username_env", "GZCTF_USERNAME"), "")
    password = os.environ.get(gz.get("password_env", "GZCTF_PASSWORD"), "")
    cookie_jar_path = gz.get("cookie_jar_path")

    if auth_mode == "password" and not (username and password):
        raise RuntimeError(
            "auth_mode='password' but credentials missing — set "
            "username_env/password_env in .env, or switch to auth_mode='cookie'/'auto'"
        )
    if auth_mode == "cookie" and not cookie_jar_path:
        raise RuntimeError(
            "auth_mode='cookie' but cookie_jar_path is empty — see "
            "runbooks/campus_sso_cookie_reuse.md for the export procedure"
        )

    return GZCTFAdapter(
        base_url=gz["base_url"],
        username=username or None,
        password=password or None,
        cookie_jar_path=cookie_jar_path,
        scope_cfg=cfg.get("scope") or {},
        submit_payload_mode=gz.get("submit_payload_mode", "auto"),
        api_public_key=gz.get("api_public_key") or None,
        default_game_id=gz.get("game_id"),
        auth_mode=auth_mode,
    )


def _build_guard(cfg: dict) -> FlagGuard:
    return FlagGuard(project_root=PROJECT, submit_cfg=cfg.get("submit", {}))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config", default=str(PROJECT / "configs" / "ai_contest.yaml"),
    )
    ap.add_argument("--healthcheck-only", action="store_true")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(
            f"config missing: {cfg_path} — copy from configs/ai_contest.example.yaml",
            file=sys.stderr,
        )
        return 2
    cfg = safe_load_file(cfg_path)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    adapter = _build_adapter(cfg)
    guard = _build_guard(cfg)
    sup = AIContestSupervisor(cfg=cfg, adapter=adapter, guard=guard)

    ok = sup.healthcheck()
    if not ok:
        print("healthcheck failed", file=sys.stderr)
        return 3
    if args.healthcheck_only:
        return 0
    return sup.run()


if __name__ == "__main__":
    raise SystemExit(main())
