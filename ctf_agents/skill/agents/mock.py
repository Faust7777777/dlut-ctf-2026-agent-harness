"""Pre-baked mock agents used by ``scripts/skill_workflow_dryrun.py`` and
the workflow unit tests.

Each agent is a callable taking a ``Challenge`` and returning either a
``FlagCandidate`` or ``None``.  ``make_mock_agent`` produces a flag with
configurable evidence/confidence so dry-runs can exercise REJECT, HOLD,
HUMAN_REVIEW, and AUTO_SUBMIT decisions.
"""
from __future__ import annotations

from typing import Callable, Optional

from ctf_agents.skill.router import Challenge
from ctf_agents.submit.flag_guard import FlagCandidate


AgentFn = Callable[[Challenge], Optional[FlagCandidate]]


def make_mock_agent(
    category: str,
    flag: str,
    *,
    evidence_count: int = 4,
    extraction_confidence: float = 1.0,
    votes: int = 3,
    risk: str = "normal",
) -> AgentFn:
    """Produce an agent that always returns the same FlagCandidate
    parameterised by the kwargs.  Use different combinations to drive
    each guard branch."""

    def agent(challenge: Challenge) -> FlagCandidate:
        return FlagCandidate(
            challenge_id=challenge.id,
            flag=flag,
            category=category,
            evidence_count=evidence_count,
            extraction_confidence=extraction_confidence,
            agent_votes=[flag] * votes,
            risk=risk,
        )

    return agent


def make_silent_agent() -> AgentFn:
    """Agent that has nothing to say (returns None)."""

    def agent(challenge: Challenge) -> Optional[FlagCandidate]:
        return None

    return agent


def make_bad_format_agent(category: str = "misc") -> AgentFn:
    """Agent producing a syntactically wrong flag — exercises REJECT."""

    def agent(challenge: Challenge) -> FlagCandidate:
        return FlagCandidate(
            challenge_id=challenge.id,
            flag="not-a-flag-format",
            category=category,
            evidence_count=1,
            extraction_confidence=0.5,
        )

    return agent


def make_low_confidence_agent(category: str, flag: str) -> AgentFn:
    """Agent producing a poor-confidence flag — exercises HOLD."""

    def agent(challenge: Challenge) -> FlagCandidate:
        return FlagCandidate(
            challenge_id=challenge.id,
            flag=flag,
            category=category,
            evidence_count=0,
            extraction_confidence=0.0,
            agent_votes=[],
        )

    return agent
