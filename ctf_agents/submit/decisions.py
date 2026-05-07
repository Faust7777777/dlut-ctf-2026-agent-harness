"""Flag-guard decision types.

Pulled out so state_store, flag_guard, force_submit, and the unit tests
can share a single source of truth for action / hold-reason vocabulary.
The state-machine itself lives in ``flag_guard.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    REJECT = "reject"
    HOLD = "hold"
    HUMAN_REVIEW = "human_review"
    AUTO_SUBMIT = "auto_submit"
    FROZEN = "frozen"


class HoldReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    RATE_LIMIT_GLOBAL = "rate_limit_global"
    RATE_LIMIT_PER_CHALLENGE = "rate_limit_per_challenge"
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    CATEGORY_NOT_AUTO = "category_not_auto"
    PWN_REVERSE_FORCED_HUMAN = "pwn_reverse_forced_human"
    UNKNOWN = "unknown"


class RejectReason(str, Enum):
    FORMAT_INVALID = "format_invalid"
    SCOPE_DENIED = "scope_denied"
    EMPTY_FLAG = "empty_flag"


@dataclass
class GuardDecision:
    action: Decision
    score: float = 0.0
    format_ok: bool = False
    reason: str = ""
    hold_reason: Optional[HoldReason] = None
    reject_reason: Optional[RejectReason] = None
    notes: list[str] = field(default_factory=list)
    challenge_id: str = ""
    flag: str = ""
    category: str = ""
    kill_switch_active: bool = False
    frozen: bool = False
    wrong_count: int = 0
    rate_limit_remaining_global_s: float = 0.0
    rate_limit_remaining_per_challenge_s: float = 0.0
    force_submit: bool = False

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "score": round(self.score, 4),
            "format_ok": self.format_ok,
            "reason": self.reason,
            "hold_reason": self.hold_reason.value if self.hold_reason else None,
            "reject_reason": self.reject_reason.value if self.reject_reason else None,
            "notes": self.notes,
            "challenge_id": self.challenge_id,
            "flag": self.flag,
            "category": self.category,
            "kill_switch_active": self.kill_switch_active,
            "frozen": self.frozen,
            "wrong_count": self.wrong_count,
            "rate_limit_remaining_global_s": round(self.rate_limit_remaining_global_s, 2),
            "rate_limit_remaining_per_challenge_s": round(self.rate_limit_remaining_per_challenge_s, 2),
            "force_submit": self.force_submit,
        }
