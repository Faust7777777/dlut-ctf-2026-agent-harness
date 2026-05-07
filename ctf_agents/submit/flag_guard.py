"""Flag guard — single point of contact between agents and the submit
adapter.  Implements §4 of the handoff plan as a closed-loop state
machine.

Public API::

    guard = FlagGuard(project_root="...", submit_cfg=cfg["submit"])
    decision = guard.decide(candidate)
    if decision.action is Decision.AUTO_SUBMIT:
        result = adapter.submit_flag(candidate.challenge_id, candidate.flag)
        guard.record_outcome(candidate, decision, correct=result.correct,
                             platform_response=result.message)

Force-submit overrides skip the score threshold, the frozen check, and
the auto-submit-categories filter, but still require ``format_ok``,
rate-limit windows, and a non-empty reason.  Every force-submit gets a
non-redacted ``force_submit`` log event.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .decisions import Decision, GuardDecision, HoldReason, RejectReason
from .kill_switch import is_active as kill_switch_active
from .state_store import SubmissionStateStore


DEFAULT_FLAG_RE = r"(?i)(flag|dlutctf)\{[^{}\s]{4,128}\}"


@dataclass
class FlagCandidate:
    challenge_id: str
    flag: str
    category: str = ""
    evidence_count: int = 0
    extraction_confidence: float = 0.0
    agent_votes: list[str] = field(default_factory=list)
    risk: str = "normal"


def format_ok(flag: str, pattern: str = DEFAULT_FLAG_RE) -> bool:
    if not flag:
        return False
    return re.fullmatch(pattern, flag.strip()) is not None


def confidence_score(c: FlagCandidate, pattern: str = DEFAULT_FLAG_RE) -> float:
    if not format_ok(c.flag, pattern):
        return 0.0
    score = 0.35
    score += min(0.25, 0.08 * max(0, c.evidence_count))
    score += max(0.0, min(0.25, c.extraction_confidence * 0.25))
    votes = c.agent_votes or []
    if votes:
        agreed = sum(1 for v in votes if v.strip() == c.flag.strip())
        score += min(0.15, 0.05 * agreed)
    if c.risk == "high":
        score -= 0.10
    if c.category in {"pwn", "reverse"}:
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 4)


_PWN_REVERSE_CATEGORIES = {"pwn", "reverse"}


class FlagGuard:
    def __init__(
        self,
        project_root: str | Path,
        submit_cfg: dict[str, Any],
        state_store: Optional[SubmissionStateStore] = None,
    ):
        self.project_root = Path(project_root)
        self.submit_cfg = submit_cfg

        state_path = self.project_root / submit_cfg.get(
            "state_path", "logs/submission_state.json"
        )
        self.state_store = state_store or SubmissionStateStore(state_path)

        kill_switch_file = submit_cfg.get("kill_switch_file", ".auto_submit_off")
        self.kill_switch_path = self.project_root / kill_switch_file

        self.flag_pattern = submit_cfg.get("flag_regex", DEFAULT_FLAG_RE)
        self.auto_submit_enabled = bool(submit_cfg.get("auto_submit", False))
        self.auto_submit_categories: set[str] = set(
            submit_cfg.get("auto_submit_categories") or []
        )
        self.min_conf_auto_submit = float(submit_cfg.get("min_conf_auto_submit", 0.92))
        self.min_conf_human_review = float(submit_cfg.get("min_conf_human_review", 0.70))
        self.max_wrong_per_challenge = int(submit_cfg.get("max_wrong_per_challenge", 2))
        self.global_window_s = float(submit_cfg.get("min_seconds_between_submits_global", 25))
        self.per_challenge_window_s = float(
            submit_cfg.get("min_seconds_between_submits_per_challenge", 90)
        )
        self.pwn_reverse_force_human_review = bool(
            submit_cfg.get("pwn_reverse_force_human_review", True)
        )
        self.force_submit_min_reason_length = int(
            submit_cfg.get("force_submit_min_reason_length", 10)
        )

    def decide(
        self,
        candidate: FlagCandidate,
        *,
        force_submit: bool = False,
        force_reason: str = "",
    ) -> GuardDecision:
        decision = GuardDecision(
            action=Decision.HOLD,
            challenge_id=candidate.challenge_id,
            flag=candidate.flag,
            category=candidate.category,
            force_submit=force_submit,
        )

        if not (candidate.flag and candidate.flag.strip()):
            decision.action = Decision.REJECT
            decision.reject_reason = RejectReason.EMPTY_FLAG
            decision.reason = "flag 为空"
            return decision

        decision.format_ok = format_ok(candidate.flag, self.flag_pattern)
        if not decision.format_ok:
            decision.action = Decision.REJECT
            decision.reject_reason = RejectReason.FORMAT_INVALID
            decision.reason = f"flag 格式不匹配 ({self.flag_pattern})"
            return decision

        decision.kill_switch_active = kill_switch_active(self.kill_switch_path)
        decision.frozen = self.state_store.is_frozen(candidate.challenge_id)
        decision.wrong_count = self.state_store.wrong_count(candidate.challenge_id)

        score = 1.0 if force_submit else confidence_score(candidate, self.flag_pattern)
        decision.score = score

        # First: compute *intent* — the action that would apply if we
        # ignored rate limits.  This isolates the "should we even try
        # auto-submitting?" decision from the atomic claim that follows.
        if force_submit:
            self._intent_force_submit(decision, candidate, force_reason)
        else:
            self._intent_normal(decision, candidate, score)

        # Second: if intent is AUTO_SUBMIT, atomically check rate windows
        # and claim a slot.  This is the single point where the race is
        # closed: only one caller per window can win the lock and pass.
        if decision.action is Decision.AUTO_SUBMIT:
            claimed, view = self.state_store.try_claim_submit_slot(
                candidate.challenge_id,
                self.global_window_s,
                self.per_challenge_window_s,
            )
            decision.rate_limit_remaining_global_s = view.remaining_global_s
            decision.rate_limit_remaining_per_challenge_s = view.remaining_per_challenge_s
            if not claimed:
                decision.action = Decision.HOLD
                if not view.globally_ok:
                    decision.hold_reason = HoldReason.RATE_LIMIT_GLOBAL
                    decision.reason = (
                        f"全局限频还差 {view.remaining_global_s:.1f}s "
                        f"(window {self.global_window_s:.0f}s)"
                    )
                    if force_submit:
                        decision.notes.append("force_submit_rate_limited_global")
                else:
                    decision.hold_reason = HoldReason.RATE_LIMIT_PER_CHALLENGE
                    decision.reason = (
                        f"单题限频还差 {view.remaining_per_challenge_s:.1f}s "
                        f"(window {self.per_challenge_window_s:.0f}s)"
                    )
                    if force_submit:
                        decision.notes.append("force_submit_rate_limited_per_challenge")
        else:
            view = self.state_store.rate_limit_view(
                candidate.challenge_id,
                self.global_window_s,
                self.per_challenge_window_s,
            )
            decision.rate_limit_remaining_global_s = view.remaining_global_s
            decision.rate_limit_remaining_per_challenge_s = view.remaining_per_challenge_s

        return decision

    def _intent_normal(
        self,
        decision: GuardDecision,
        candidate: FlagCandidate,
        score: float,
    ) -> None:
        if decision.frozen:
            decision.action = Decision.HUMAN_REVIEW
            decision.reason = "题已冻结（max_wrong 已触发），新候选默认人审"
            decision.notes.append("frozen_human_review_required")
            return

        if score < self.min_conf_human_review:
            decision.action = Decision.HOLD
            decision.hold_reason = HoldReason.LOW_CONFIDENCE
            decision.reason = (
                f"score {score:.2f} < {self.min_conf_human_review:.2f} (min_conf_human_review)"
            )
            return

        if score < self.min_conf_auto_submit:
            decision.action = Decision.HUMAN_REVIEW
            decision.reason = (
                f"score {score:.2f} ∈ [{self.min_conf_human_review:.2f}, "
                f"{self.min_conf_auto_submit:.2f}) → 需人工确认"
            )
            return

        if candidate.category in _PWN_REVERSE_CATEGORIES and self.pwn_reverse_force_human_review:
            decision.action = Decision.HUMAN_REVIEW
            decision.hold_reason = HoldReason.PWN_REVERSE_FORCED_HUMAN
            decision.reason = "Pwn/Reverse 强制人审"
            decision.notes.append("pwn_reverse_forced_human")
            return

        if decision.kill_switch_active:
            decision.action = Decision.HUMAN_REVIEW
            decision.hold_reason = HoldReason.KILL_SWITCH_ACTIVE
            decision.reason = "kill switch 文件存在 → 自动提交全局降级"
            decision.notes.append("kill_switch_active")
            return

        if not self.auto_submit_enabled:
            decision.action = Decision.HUMAN_REVIEW
            decision.reason = "auto_submit=false，所有提交需人审"
            return

        if (
            self.auto_submit_categories
            and candidate.category not in self.auto_submit_categories
        ):
            decision.action = Decision.HUMAN_REVIEW
            decision.hold_reason = HoldReason.CATEGORY_NOT_AUTO
            decision.reason = (
                f"category={candidate.category!r} 不在 auto_submit_categories"
                f" {sorted(self.auto_submit_categories)} 内"
            )
            return

        decision.action = Decision.AUTO_SUBMIT
        decision.reason = "格式 OK、置信度足、类别允许（限频在外层 atomic claim 内核验）"

    def _intent_force_submit(
        self,
        decision: GuardDecision,
        candidate: FlagCandidate,
        reason: str,
    ) -> None:
        if not reason or len(reason.strip()) < self.force_submit_min_reason_length:
            decision.action = Decision.REJECT
            decision.reject_reason = RejectReason.FORMAT_INVALID
            decision.reason = (
                f"force_submit 需要 reason ≥ {self.force_submit_min_reason_length} 字符"
            )
            decision.notes.append("force_submit_reason_too_short")
            return

        decision.action = Decision.AUTO_SUBMIT
        decision.reason = (
            f"force_submit override 通过 (frozen={decision.frozen}, "
            f"category={candidate.category!r}, reason={reason!r})"
        )
        decision.notes.append("force_submit_override")

    def record_outcome(
        self,
        candidate: FlagCandidate,
        decision: GuardDecision,
        correct: Optional[bool],
        platform_response: str = "",
    ) -> dict:
        return self.state_store.record_submit(
            challenge_id=candidate.challenge_id,
            flag=candidate.flag,
            correct=correct,
            max_wrong=self.max_wrong_per_challenge,
            force=decision.force_submit,
            platform_response=platform_response,
        )


def decide(candidate: FlagCandidate, submit_cfg: dict[str, Any]) -> dict:
    """Backward-compat wrapper used by the existing CLI/dry-run paths.

    Returns a plain dict (legacy shape) but routes through the new state
    machine via a transient ``FlagGuard`` instance against an in-memory
    state store path.  Production callers should use ``FlagGuard``
    directly so the state file persists.
    """
    project_root = Path(__file__).resolve().parents[2]
    guard = FlagGuard(project_root=project_root, submit_cfg=submit_cfg)
    decision = guard.decide(candidate)
    return decision.to_dict()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="flag candidate decision driver (new state machine)"
    )
    ap.add_argument("flag")
    ap.add_argument("--challenge-id", default="dryrun")
    ap.add_argument("--category", default="misc")
    ap.add_argument("--evidence-count", type=int, default=1)
    ap.add_argument("--extraction-confidence", type=float, default=0.85)
    ap.add_argument("--vote", action="append", default=[])
    ap.add_argument("--risk", default="normal", choices=["normal", "high"])
    ap.add_argument(
        "--config",
        default="configs/config.yaml",
        help="YAML config (top-level submit: stanza is read)",
    )
    ap.add_argument("--force", action="store_true", help="force_submit override")
    ap.add_argument("--reason", default="", help="force_submit reason")
    args = ap.parse_args()

    import yaml  # late import; not all callers have yaml installed

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    project_root = Path(args.config).resolve().parent.parent
    guard = FlagGuard(project_root=project_root, submit_cfg=cfg.get("submit", {}))
    cand = FlagCandidate(
        challenge_id=args.challenge_id,
        flag=args.flag,
        category=args.category,
        evidence_count=args.evidence_count,
        extraction_confidence=args.extraction_confidence,
        agent_votes=args.vote,
        risk=args.risk,
    )
    decision = guard.decide(cand, force_submit=args.force, force_reason=args.reason)
    print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
