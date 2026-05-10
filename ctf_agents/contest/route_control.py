"""Pure route-control state machine for the AI-identity harness."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional


ROUTE_STATE_SCHEMA_VERSION = 1


class ProgressType(str, Enum):
    CHALLENGE_PROGRESS = "challenge_progress"
    CAPABILITY_PROGRESS = "capability_progress"
    NEGATIVE_PROGRESS = "negative_progress"
    NO_PROGRESS = "no_progress"
    PLATFORM_PROGRESS = "platform_progress"
    DOCUMENTATION_PROGRESS = "documentation_progress"


class FailureType(str, Enum):
    PARAMETRIC_FAILURE = "parameteric_failure"
    STRUCTURAL_FAILURE = "structural_failure"
    TOOL_FAILURE = "tool_failure"
    REPRESENTATION_MISMATCH = "representation_mismatch"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    WRONG_TARGET = "wrong_target"
    HELPER_BOUND_LIMIT = "helper_bound_limit"


class RouteDecision(str, Enum):
    CONTINUE_ROUTE = "continue_route"
    CUT_ROUTE = "cut_route"
    SWITCH_FAMILY = "switch_family"
    SPAWN_PUBLIC_SEARCH = "spawn_public_search"
    SPAWN_EXPERT_REVIEW = "spawn_expert_review"
    SPAWN_PERSISTENT_LANE = "spawn_persistent_lane"
    BLOCK_NO_CANDIDATE = "block_no_candidate"
    ALLOW_NO_CANDIDATE = "allow_no_candidate"


class RoutePhase(str, Enum):
    ACTIVE = "active"
    CUT = "cut"
    EXHAUSTED = "exhausted"


class PublicSearchStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    RUNNING = "running"
    COMPLETE = "complete"
    BLOCKED_BY_RULES = "blocked_by_rules"


class ExpertReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    RUNNING = "running"
    COMPLETE = "complete"


class PersistentLaneStatus(str, Enum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETE = "complete"
    STALE = "stale"
    STOPPED = "stopped"


_NEGATIVE_BOUND_MARKERS = (
    "negative bound certificate",
    "bound certificate negative",
    "bound check negative",
    "negative bound",
    "negative_bound",
    "bound negative",
)

_ROUTE_START_CONTRACT = {
    "hypothesis": "route family may fit available artifacts",
    "continue_if": "challenge evidence delta is observed",
    "cut_if": "route assumptions fail, helper bounds are exceeded, or no evidence delta repeats",
    "expected_evidence_delta": "new candidate evidence or qualified negative evidence",
}


def _apply_route_start_contract(entry: "FamilyLedgerEntry") -> "FamilyLedgerEntry":
    if not entry.hypothesis:
        entry.hypothesis = _ROUTE_START_CONTRACT["hypothesis"]
    if not entry.continue_if:
        entry.continue_if = _ROUTE_START_CONTRACT["continue_if"]
    if not entry.cut_if:
        entry.cut_if = _ROUTE_START_CONTRACT["cut_if"]
    if not entry.expected_evidence_delta:
        entry.expected_evidence_delta = _ROUTE_START_CONTRACT["expected_evidence_delta"]
    return entry


@dataclass
class FamilyLedgerEntry:
    route_id: str
    family: str
    status: str = "active"
    reason: str = ""
    hypothesis: str = ""
    continue_if: str = ""
    cut_if: str = ""
    expected_evidence_delta: str = ""
    started_at_cycle: int = 0
    ended_at_cycle: Optional[int] = None
    experiments: list[dict[str, Any]] = field(default_factory=list)
    failure_type: Optional[str] = None
    failure_signals: list[str] = field(default_factory=list)
    cut_reason: Optional[str] = None
    exhaustion_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "family": self.family,
            "status": self.status,
            "reason": self.reason,
            "hypothesis": self.hypothesis,
            "continue_if": self.continue_if,
            "cut_if": self.cut_if,
            "expected_evidence_delta": self.expected_evidence_delta,
            "started_at_cycle": self.started_at_cycle,
            "ended_at_cycle": self.ended_at_cycle,
            "experiments": list(self.experiments),
            "failure_type": self.failure_type,
            "failure_signals": list(self.failure_signals),
            "cut_reason": self.cut_reason,
            "exhaustion_reason": self.exhaustion_reason,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FamilyLedgerEntry":
        return cls(
            route_id=str(payload.get("route_id", "")),
            family=str(payload.get("family", "")),
            status=str(payload.get("status", "active")),
            reason=str(payload.get("reason", "")),
            hypothesis=str(payload.get("hypothesis", "")),
            continue_if=str(payload.get("continue_if", "")),
            cut_if=str(payload.get("cut_if", "")),
            expected_evidence_delta=str(payload.get("expected_evidence_delta", "")),
            started_at_cycle=int(payload.get("started_at_cycle", 0) or 0),
            ended_at_cycle=_maybe_int(payload.get("ended_at_cycle")),
            experiments=[dict(item) for item in (payload.get("experiments") or []) if isinstance(item, dict)],
            failure_type=_maybe_str(payload.get("failure_type")),
            failure_signals=[str(item) for item in (payload.get("failure_signals") or []) if item is not None],
            cut_reason=_maybe_str(payload.get("cut_reason")),
            exhaustion_reason=_maybe_str(payload.get("exhaustion_reason")),
        )


@dataclass
class SearchLedgerEntry:
    query: str
    disposition: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "disposition": self.disposition,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SearchLedgerEntry":
        return cls(
            query=str(payload.get("query", "")),
            disposition=str(payload.get("disposition", "")),
            notes=str(payload.get("notes", "")),
        )


@dataclass
class ExpertReviewPacket:
    challenge_summary: str = ""
    current_family: str = ""
    tried_families: list[str] = field(default_factory=list)
    experiments: list[dict[str, Any]] = field(default_factory=list)
    evidence_delta: int = 0
    failure_signals: list[str] = field(default_factory=list)
    public_search_summary: list[SearchLedgerEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_summary": self.challenge_summary,
            "current_family": self.current_family,
            "tried_families": list(self.tried_families),
            "experiments": list(self.experiments),
            "evidence_delta": self.evidence_delta,
            "failure_signals": list(self.failure_signals),
            "public_search_summary": [entry.to_dict() for entry in self.public_search_summary],
        }


@dataclass
class PersistentLaneState:
    status: PersistentLaneStatus = PersistentLaneStatus.NOT_STARTED
    open_questions: list[str] = field(default_factory=list)
    alternative_families: list[str] = field(default_factory=list)
    public_search_ledger: list[SearchLedgerEntry] = field(default_factory=list)
    helper_evaluation: list[str] = field(default_factory=list)
    negative_evidence: list[str] = field(default_factory=list)
    no_candidate_blockers: list[str] = field(default_factory=list)
    stop_report_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "open_questions": list(self.open_questions),
            "alternative_families": list(self.alternative_families),
            "public_search_ledger": [entry.to_dict() for entry in self.public_search_ledger],
            "helper_evaluation": list(self.helper_evaluation),
            "negative_evidence": list(self.negative_evidence),
            "no_candidate_blockers": list(self.no_candidate_blockers),
            "stop_report_path": self.stop_report_path,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PersistentLaneState":
        return cls(
            status=_enum_value(PersistentLaneStatus, payload.get("status"), PersistentLaneStatus.NOT_STARTED),
            open_questions=[str(item) for item in (payload.get("open_questions") or []) if item is not None],
            alternative_families=[str(item) for item in (payload.get("alternative_families") or []) if item is not None],
            public_search_ledger=[
                SearchLedgerEntry.from_dict(item)
                for item in (payload.get("public_search_ledger") or [])
                if isinstance(item, dict)
            ],
            helper_evaluation=[str(item) for item in (payload.get("helper_evaluation") or []) if item is not None],
            negative_evidence=[str(item) for item in (payload.get("negative_evidence") or []) if item is not None],
            no_candidate_blockers=[str(item) for item in (payload.get("no_candidate_blockers") or []) if item is not None],
            stop_report_path=_maybe_str(payload.get("stop_report_path")),
        )


@dataclass
class RouteState:
    current_family: str
    schema_version: int = ROUTE_STATE_SCHEMA_VERSION
    tried_families: list[FamilyLedgerEntry] = field(default_factory=list)
    failure_type: Optional[FailureType] = None
    failure_signals: list[str] = field(default_factory=list)
    evidence_delta_score: int = 0
    same_family_no_delta_count: int = 0
    trivial_root_count: int = 0
    next_family: Optional[str] = None
    route_decision: RouteDecision = RouteDecision.CONTINUE_ROUTE
    pending_actions: list[RouteDecision] = field(default_factory=list)
    route_phase: RoutePhase = RoutePhase.ACTIVE
    route_cycle: int = 0
    route_budget_remaining: int = 0
    public_search_status: PublicSearchStatus = PublicSearchStatus.NOT_REQUIRED
    expert_review_status: ExpertReviewStatus = ExpertReviewStatus.NOT_REQUIRED
    persistent_lane_status: PersistentLaneStatus = PersistentLaneStatus.NOT_STARTED
    no_candidate_blockers: list[str] = field(default_factory=list)
    route_id_counter: int = 0
    candidate_queue_empty: bool = False
    local_baseline_done: bool = False
    short_codex_done: bool = False
    family_switch_done: bool = False
    family_switch_justified_impossible: bool = False
    family_switch_impossible_reason: Optional[str] = None
    persistent_lane: PersistentLaneState = field(default_factory=PersistentLaneState)

    @classmethod
    def new(cls, *, current_family: str, route_budget_remaining: int = 0) -> "RouteState":
        return cls(
            schema_version=ROUTE_STATE_SCHEMA_VERSION,
            current_family=current_family,
            route_budget_remaining=route_budget_remaining,
            route_phase=RoutePhase.ACTIVE,
            route_decision=RouteDecision.CONTINUE_ROUTE,
        )

    def ensure_family_entry(self, *, reason: str = "") -> FamilyLedgerEntry:
        if self.tried_families and self.tried_families[-1].family == self.current_family:
            return _apply_route_start_contract(self.tried_families[-1])
        self.route_id_counter += 1
        entry = _apply_route_start_contract(
            FamilyLedgerEntry(
                route_id=f"route_{self.route_id_counter:03d}",
                family=self.current_family,
                reason=reason or f"entered {self.current_family}",
                started_at_cycle=self.route_cycle or 0,
            )
        )
        self.tried_families.append(entry)
        return entry

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "current_family": self.current_family,
            "tried_families": [entry.to_dict() for entry in self.tried_families],
            "failure_type": _enum_to_value(self.failure_type),
            "failure_signals": list(self.failure_signals),
            "evidence_delta_score": self.evidence_delta_score,
            "same_family_no_delta_count": self.same_family_no_delta_count,
            "trivial_root_count": self.trivial_root_count,
            "next_family": self.next_family,
            "route_decision": _enum_to_value(self.route_decision),
            "pending_actions": [_enum_to_value(action) for action in self.pending_actions],
            "route_phase": _enum_to_value(self.route_phase),
            "route_cycle": self.route_cycle,
            "route_budget_remaining": self.route_budget_remaining,
            "public_search_status": _enum_to_value(self.public_search_status),
            "expert_review_status": _enum_to_value(self.expert_review_status),
            "persistent_lane_status": _enum_to_value(self.persistent_lane_status),
            "no_candidate_blockers": list(self.no_candidate_blockers),
            "route_id_counter": self.route_id_counter,
            "candidate_queue_empty": self.candidate_queue_empty,
            "local_baseline_done": self.local_baseline_done,
            "short_codex_done": self.short_codex_done,
            "family_switch_done": self.family_switch_done,
            "family_switch_justified_impossible": self.family_switch_justified_impossible,
            "family_switch_impossible_reason": self.family_switch_impossible_reason,
            "persistent_lane": self.persistent_lane.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RouteState":
        if not isinstance(payload, dict):
            raise TypeError("RouteState payload must be a dict")
        persistent_lane_payload = payload.get("persistent_lane")
        persistent_lane = (
            PersistentLaneState.from_dict(persistent_lane_payload)
            if isinstance(persistent_lane_payload, dict)
            else PersistentLaneState()
        )
        return cls(
            schema_version=int(payload.get("schema_version", ROUTE_STATE_SCHEMA_VERSION) or ROUTE_STATE_SCHEMA_VERSION),
            current_family=str(payload.get("current_family", "")),
            tried_families=[
                FamilyLedgerEntry.from_dict(item)
                for item in (payload.get("tried_families") or [])
                if isinstance(item, dict)
            ],
            failure_type=_enum_value(FailureType, payload.get("failure_type"), None),
            failure_signals=[str(item) for item in (payload.get("failure_signals") or []) if item is not None],
            evidence_delta_score=int(payload.get("evidence_delta_score", 0) or 0),
            same_family_no_delta_count=int(payload.get("same_family_no_delta_count", 0) or 0),
            trivial_root_count=int(payload.get("trivial_root_count", 0) or 0),
            next_family=_maybe_str(payload.get("next_family")),
            route_decision=_enum_value(RouteDecision, payload.get("route_decision"), RouteDecision.CONTINUE_ROUTE),
            pending_actions=[
                action
                for action in (
                    _enum_value(RouteDecision, item, None)
                    for item in (payload.get("pending_actions") or [])
                )
                if action is not None
            ],
            route_phase=_enum_value(RoutePhase, payload.get("route_phase"), RoutePhase.ACTIVE),
            route_cycle=int(payload.get("route_cycle", 0) or 0),
            route_budget_remaining=int(payload.get("route_budget_remaining", 0) or 0),
            public_search_status=_enum_value(PublicSearchStatus, payload.get("public_search_status"), PublicSearchStatus.NOT_REQUIRED),
            expert_review_status=_enum_value(ExpertReviewStatus, payload.get("expert_review_status"), ExpertReviewStatus.NOT_REQUIRED),
            persistent_lane_status=_enum_value(PersistentLaneStatus, payload.get("persistent_lane_status"), PersistentLaneStatus.NOT_STARTED),
            no_candidate_blockers=[str(item) for item in (payload.get("no_candidate_blockers") or []) if item is not None],
            route_id_counter=int(payload.get("route_id_counter", 0) or 0),
            candidate_queue_empty=bool(payload.get("candidate_queue_empty", False)),
            local_baseline_done=bool(payload.get("local_baseline_done", False)),
            short_codex_done=bool(payload.get("short_codex_done", False)),
            family_switch_done=bool(payload.get("family_switch_done", False)),
            family_switch_justified_impossible=bool(payload.get("family_switch_justified_impossible", False)),
            family_switch_impossible_reason=_maybe_str(payload.get("family_switch_impossible_reason")),
            persistent_lane=persistent_lane,
        )


def classify_progress(
    *,
    evidence_delta_score: int = 0,
    challenge_advanced: bool = False,
    negative_evidence_quality: str = "",
    helper_smoke_passed: bool = False,
    platform_change: bool = False,
    documentation_only: bool = False,
) -> ProgressType:
    if challenge_advanced and evidence_delta_score > 0:
        return ProgressType.CHALLENGE_PROGRESS
    if negative_evidence_quality in {"high", "qualified", "qualified_negative"}:
        return ProgressType.NEGATIVE_PROGRESS
    if helper_smoke_passed:
        return ProgressType.CAPABILITY_PROGRESS
    if platform_change:
        return ProgressType.PLATFORM_PROGRESS
    if documentation_only:
        return ProgressType.DOCUMENTATION_PROGRESS
    return ProgressType.NO_PROGRESS


def classify_failure(signals: Iterable[str]) -> FailureType:
    joined = " ".join(str(item).lower() for item in signals if item is not None)
    if "wrong target" in joined or "wrong_target" in joined:
        return FailureType.WRONG_TARGET
    if "structural" in joined or "degree" in joined or "shape" in joined or "mismatch" in joined:
        if "representation" in joined and not any(
            marker in joined for marker in ("structural", "degree", "shape")
        ):
            return FailureType.REPRESENTATION_MISMATCH
        return FailureType.STRUCTURAL_FAILURE
    if "representation" in joined:
        return FailureType.REPRESENTATION_MISMATCH
    if (
        "helper bound limit" in joined
        or "helper_bound_limit" in joined
        or "trivial root" in joined
        or any(marker in joined for marker in _NEGATIVE_BOUND_MARKERS)
    ):
        return FailureType.HELPER_BOUND_LIMIT
    if "tool" in joined or "missing runtime" in joined or "runtime" in joined:
        return FailureType.TOOL_FAILURE
    if "parameter" in joined or "parametric" in joined:
        return FailureType.PARAMETRIC_FAILURE
    return FailureType.EVIDENCE_INSUFFICIENT


def should_trigger_public_search(state: RouteState) -> bool:
    if state.public_search_status in {
        PublicSearchStatus.COMPLETE,
        PublicSearchStatus.RUNNING,
        PublicSearchStatus.BLOCKED_BY_RULES,
    }:
        return False
    if state.public_search_status is PublicSearchStatus.REQUIRED:
        return True
    if state.failure_type in {FailureType.STRUCTURAL_FAILURE, FailureType.HELPER_BOUND_LIMIT}:
        return True
    if state.same_family_no_delta_count >= 2 and state.public_search_status is not PublicSearchStatus.COMPLETE:
        return True
    if state.route_decision is RouteDecision.SPAWN_PUBLIC_SEARCH:
        return True
    if state.route_phase is RoutePhase.CUT and state.public_search_status is not PublicSearchStatus.COMPLETE:
        return True
    return False


def should_trigger_expert_review(state: RouteState) -> bool:
    if state.expert_review_status in {
        ExpertReviewStatus.COMPLETE,
        ExpertReviewStatus.RUNNING,
    }:
        return False
    if state.expert_review_status is ExpertReviewStatus.REQUIRED:
        return True
    if state.public_search_status in {
        PublicSearchStatus.COMPLETE,
        PublicSearchStatus.BLOCKED_BY_RULES,
    } and state.expert_review_status is ExpertReviewStatus.NOT_REQUIRED:
        return True
    if state.route_decision is RouteDecision.SPAWN_EXPERT_REVIEW:
        return True
    if state.route_phase is RoutePhase.EXHAUSTED and state.expert_review_status is not ExpertReviewStatus.COMPLETE:
        return True
    return False


def should_cut_route(state: RouteState) -> bool:
    if state.route_phase is RoutePhase.EXHAUSTED:
        return False
    if state.failure_type is FailureType.HELPER_BOUND_LIMIT:
        return True
    if state.failure_type is FailureType.STRUCTURAL_FAILURE:
        return True
    joined_signals = " ".join(str(item).lower() for item in state.failure_signals if item is not None)
    if state.failure_type is FailureType.STRUCTURAL_FAILURE and any(
        marker in joined_signals for marker in _NEGATIVE_BOUND_MARKERS
    ):
        return True
    if state.trivial_root_count >= 2:
        return True
    if state.same_family_no_delta_count >= 2 and state.failure_type in {
        FailureType.WRONG_TARGET,
        FailureType.REPRESENTATION_MISMATCH,
        FailureType.HELPER_BOUND_LIMIT,
    }:
        return True
    if state.route_budget_remaining < 0:
        return True
    return False


def choose_next_family(state: RouteState) -> Optional[str]:
    if state.next_family:
        return state.next_family
    if state.tried_families:
        last = state.tried_families[-1].family
        if last != state.current_family:
            return last
    if state.current_family.endswith(".initial"):
        return None
    return f"{state.current_family}.alt"


def has_route_exhaustion_certificate(state: RouteState) -> bool:
    if state.route_phase is not RoutePhase.EXHAUSTED:
        return False
    for entry in state.tried_families:
        if (
            entry.family == state.current_family
            and entry.status == "exhausted"
            and entry.ended_at_cycle is not None
            and bool(entry.failure_type)
            and bool(entry.failure_signals)
            and bool(entry.exhaustion_reason)
        ):
            return True
    return False


def _family_switch_requirement_met(state: RouteState) -> bool:
    if state.family_switch_done:
        return True
    return bool(
        state.family_switch_justified_impossible
        and state.family_switch_impossible_reason
    )


def _persistent_lane_complete_for_no_candidate(state: RouteState) -> bool:
    if state.persistent_lane_status in {PersistentLaneStatus.COMPLETE, PersistentLaneStatus.SUSPENDED}:
        return not state.persistent_lane.no_candidate_blockers
    if state.persistent_lane_status in {PersistentLaneStatus.STALE, PersistentLaneStatus.STOPPED}:
        return bool(state.persistent_lane.stop_report_path) and not state.persistent_lane.no_candidate_blockers
    return False


def can_emit_no_candidate(state: RouteState) -> bool:
    if not state.local_baseline_done:
        return False
    if not state.short_codex_done:
        return False
    if not _family_switch_requirement_met(state):
        return False
    if state.failure_type is None:
        return False
    if state.public_search_status not in {
        PublicSearchStatus.COMPLETE,
        PublicSearchStatus.BLOCKED_BY_RULES,
    }:
        return False
    if state.expert_review_status is not ExpertReviewStatus.COMPLETE:
        return False
    if not _persistent_lane_complete_for_no_candidate(state):
        return False
    if not state.candidate_queue_empty:
        return False
    if state.no_candidate_blockers:
        return False
    return has_route_exhaustion_certificate(state)


def _drop_resolved_no_candidate_blockers(state: RouteState) -> None:
    resolved: set[str] = set()
    if state.route_phase is RoutePhase.EXHAUSTED:
        resolved.update(
            {
                "route_not_exhausted",
                "route_cut",
                "same_family_stalled_twice",
                "trivial_root_repetition",
            }
        )
        if state.failure_type is not None:
            resolved.add("helper_bound_limit")
    if has_route_exhaustion_certificate(state):
        resolved.add("route_exhaustion_unproven")
    if state.public_search_status in {
        PublicSearchStatus.COMPLETE,
        PublicSearchStatus.BLOCKED_BY_RULES,
    }:
        resolved.add("public_search_required")
    if state.expert_review_status is ExpertReviewStatus.COMPLETE:
        resolved.add("expert_review_required")
    if _persistent_lane_complete_for_no_candidate(state):
        resolved.add("persistent_lane_active")
        resolved.add("persistent_lane_blockers")
        resolved.add("persistent_lane_stop_report_required")
    if _family_switch_requirement_met(state):
        resolved.add("family_switch_required")
    if state.candidate_queue_empty:
        resolved.add("candidate_queue_not_empty")
    if resolved:
        state.no_candidate_blockers = [
            blocker for blocker in state.no_candidate_blockers if blocker not in resolved
        ]


def _add_blocker(state: RouteState, blocker: str) -> None:
    if blocker not in state.no_candidate_blockers:
        state.no_candidate_blockers.append(blocker)


def _queue_action(state: RouteState, action: RouteDecision) -> None:
    if action not in state.pending_actions:
        state.pending_actions.append(action)


def _request_persistent_lane(state: RouteState) -> None:
    if state.persistent_lane_status is PersistentLaneStatus.NOT_STARTED:
        state.persistent_lane_status = PersistentLaneStatus.ACTIVE
        state.persistent_lane.status = PersistentLaneStatus.ACTIVE
        _queue_action(state, RouteDecision.SPAWN_PERSISTENT_LANE)


_FAILURE_STRENGTH = {
    FailureType.EVIDENCE_INSUFFICIENT: 0,
    FailureType.PARAMETRIC_FAILURE: 1,
    FailureType.TOOL_FAILURE: 2,
    FailureType.HELPER_BOUND_LIMIT: 3,
    FailureType.REPRESENTATION_MISMATCH: 4,
    FailureType.STRUCTURAL_FAILURE: 5,
    FailureType.WRONG_TARGET: 6,
}


def _stronger_failure_type(
    current: Optional[FailureType],
    incoming: Optional[FailureType],
) -> Optional[FailureType]:
    if current is None:
        return incoming
    if incoming is None:
        return current
    if _FAILURE_STRENGTH[incoming] > _FAILURE_STRENGTH[current]:
        return incoming
    return current


def evaluate_route(
    state: RouteState,
    *,
    progress: ProgressType = ProgressType.NO_PROGRESS,
    evidence_delta_score: Optional[int] = None,
    failure_type: Optional[FailureType | str] = None,
    failure_signals: Optional[Iterable[str]] = None,
    consider_no_candidate: bool = False,
) -> RouteState:
    next_state = RouteState.from_dict(state.to_dict())
    next_state.route_cycle += 1
    if evidence_delta_score is not None:
        next_state.evidence_delta_score = int(evidence_delta_score)
    if failure_type is not None:
        incoming_failure = _enum_value(FailureType, failure_type, FailureType.EVIDENCE_INSUFFICIENT)
        next_state.failure_type = _stronger_failure_type(next_state.failure_type, incoming_failure)
    if failure_signals is not None:
        next_state.failure_signals = [str(item) for item in failure_signals if item is not None]
    if progress is ProgressType.CHALLENGE_PROGRESS:
        next_state.same_family_no_delta_count = 0
        next_state.evidence_delta_score = max(next_state.evidence_delta_score, 1)
    elif progress is ProgressType.NEGATIVE_PROGRESS:
        next_state.same_family_no_delta_count = 0
    else:
        next_state.same_family_no_delta_count += 1

    joined_signals = " ".join(next_state.failure_signals).lower()
    if next_state.failure_signals:
        next_state.failure_type = _stronger_failure_type(
            next_state.failure_type,
            classify_failure(next_state.failure_signals),
        )
    if "trivial_root" in joined_signals or "trivial root" in joined_signals:
        next_state.trivial_root_count += 1

    entry = next_state.ensure_family_entry()

    if should_cut_route(next_state):
        next_state.route_phase = RoutePhase.CUT
        next_state.route_decision = RouteDecision.CUT_ROUTE
        _queue_action(next_state, RouteDecision.CUT_ROUTE)
        _request_persistent_lane(next_state)
        entry.status = "cut"
        entry.failure_type = _enum_to_value(next_state.failure_type)
        entry.failure_signals = list(next_state.failure_signals)
        entry.cut_reason = _enum_to_value(next_state.failure_type) or "cut"
        entry.ended_at_cycle = next_state.route_cycle
        _add_blocker(next_state, "route_cut")
        if next_state.failure_type is FailureType.HELPER_BOUND_LIMIT:
            _add_blocker(next_state, "helper_bound_limit")
        if next_state.trivial_root_count >= 2:
            _add_blocker(next_state, "trivial_root_repetition")
        if next_state.public_search_status in {
            PublicSearchStatus.COMPLETE,
            PublicSearchStatus.BLOCKED_BY_RULES,
        }:
            next_family = choose_next_family(next_state)
            if next_family:
                next_state.next_family = next_family
                next_state.route_decision = RouteDecision.SWITCH_FAMILY
                _queue_action(next_state, RouteDecision.SWITCH_FAMILY)
            else:
                next_state.route_phase = RoutePhase.EXHAUSTED
                entry.status = "exhausted"
                next_state.route_decision = RouteDecision.BLOCK_NO_CANDIDATE
        else:
            next_state.public_search_status = PublicSearchStatus.REQUIRED
            _add_blocker(next_state, "public_search_required")
            if next_state.failure_type is not FailureType.STRUCTURAL_FAILURE:
                next_state.route_decision = RouteDecision.SPAWN_PUBLIC_SEARCH
            _queue_action(next_state, RouteDecision.SPAWN_PUBLIC_SEARCH)

    if next_state.same_family_no_delta_count >= 2:
        _add_blocker(next_state, "same_family_stalled_twice")
        if next_state.public_search_status is PublicSearchStatus.NOT_REQUIRED:
            next_state.public_search_status = PublicSearchStatus.REQUIRED
        if next_state.route_decision is RouteDecision.CONTINUE_ROUTE:
            next_state.route_decision = RouteDecision.SPAWN_PUBLIC_SEARCH
            _queue_action(next_state, RouteDecision.SPAWN_PUBLIC_SEARCH)

    if should_trigger_public_search(next_state):
        next_state.public_search_status = PublicSearchStatus.REQUIRED
        _add_blocker(next_state, "public_search_required")
        if next_state.route_decision is RouteDecision.CONTINUE_ROUTE:
            next_state.route_decision = RouteDecision.SPAWN_PUBLIC_SEARCH
        _queue_action(next_state, RouteDecision.SPAWN_PUBLIC_SEARCH)

    if next_state.public_search_status in {
        PublicSearchStatus.COMPLETE,
        PublicSearchStatus.BLOCKED_BY_RULES,
    } and should_trigger_expert_review(next_state):
        next_state.expert_review_status = ExpertReviewStatus.REQUIRED
        _add_blocker(next_state, "expert_review_required")
        if next_state.route_decision in {RouteDecision.CONTINUE_ROUTE, RouteDecision.SPAWN_PUBLIC_SEARCH}:
            next_state.route_decision = RouteDecision.SPAWN_EXPERT_REVIEW
        _queue_action(next_state, RouteDecision.SPAWN_EXPERT_REVIEW)

    if consider_no_candidate:
        _drop_resolved_no_candidate_blockers(next_state)
        if can_emit_no_candidate(next_state):
            next_state.route_decision = RouteDecision.ALLOW_NO_CANDIDATE
            next_state.no_candidate_blockers = []
            next_state.route_phase = RoutePhase.EXHAUSTED
        else:
            if next_state.route_phase is not RoutePhase.EXHAUSTED:
                _add_blocker(next_state, "route_not_exhausted")
            public_search_done = next_state.public_search_status in {
                PublicSearchStatus.COMPLETE,
                PublicSearchStatus.BLOCKED_BY_RULES,
            }
            expert_review_done = next_state.expert_review_status is ExpertReviewStatus.COMPLETE
            public_search_needs_spawn = next_state.public_search_status in {
                PublicSearchStatus.NOT_REQUIRED,
                PublicSearchStatus.REQUIRED,
            }
            expert_review_needs_spawn = public_search_done and next_state.expert_review_status in {
                ExpertReviewStatus.NOT_REQUIRED,
                ExpertReviewStatus.REQUIRED,
            }
            if not public_search_done:
                if next_state.public_search_status is not PublicSearchStatus.RUNNING:
                    next_state.public_search_status = PublicSearchStatus.REQUIRED
                _add_blocker(next_state, "public_search_required")
            if not expert_review_done:
                if next_state.expert_review_status is not ExpertReviewStatus.RUNNING:
                    next_state.expert_review_status = ExpertReviewStatus.REQUIRED
                _add_blocker(next_state, "expert_review_required")
            if next_state.persistent_lane_status is PersistentLaneStatus.ACTIVE:
                _add_blocker(next_state, "persistent_lane_active")
            elif (
                next_state.persistent_lane_status
                in {PersistentLaneStatus.STALE, PersistentLaneStatus.STOPPED}
                and not next_state.persistent_lane.stop_report_path
            ):
                _add_blocker(next_state, "persistent_lane_stop_report_required")
            elif next_state.persistent_lane_status is PersistentLaneStatus.NOT_STARTED and public_search_done and expert_review_done:
                _request_persistent_lane(next_state)
                _add_blocker(next_state, "persistent_lane_required")
            if next_state.persistent_lane.no_candidate_blockers:
                _add_blocker(next_state, "persistent_lane_blockers")
            if not _family_switch_requirement_met(next_state):
                _add_blocker(next_state, "family_switch_required")
            if not next_state.candidate_queue_empty:
                _add_blocker(next_state, "candidate_queue_not_empty")
            if next_state.route_phase is RoutePhase.EXHAUSTED and not has_route_exhaustion_certificate(next_state):
                _add_blocker(next_state, "route_exhaustion_unproven")
            if public_search_needs_spawn:
                next_state.route_decision = RouteDecision.SPAWN_PUBLIC_SEARCH
                _queue_action(next_state, RouteDecision.SPAWN_PUBLIC_SEARCH)
            elif expert_review_needs_spawn:
                next_state.route_decision = RouteDecision.SPAWN_EXPERT_REVIEW
                _queue_action(next_state, RouteDecision.SPAWN_EXPERT_REVIEW)
            elif RouteDecision.SPAWN_PERSISTENT_LANE in next_state.pending_actions:
                next_state.route_decision = RouteDecision.SPAWN_PERSISTENT_LANE
            else:
                next_state.route_decision = RouteDecision.BLOCK_NO_CANDIDATE
                if next_state.route_phase is RoutePhase.ACTIVE:
                    next_state.route_phase = RoutePhase.CUT
    return next_state


def _maybe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value)
    return value if value else None


def _enum_value(enum_cls, value: Any, default):
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return default
    try:
        return enum_cls(str(value))
    except ValueError:
        return default


def _enum_to_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value
