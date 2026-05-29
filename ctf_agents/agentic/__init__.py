"""Minimal agentic layer: a real ReAct loop + tool-calling + an in-loop
verify/submit gate.

This is the piece the harness was missing: instead of a deterministic
``route -> built-in agent -> guard`` pipeline, the model drives a
``reason -> call tool -> observe -> repeat`` loop and chooses what to do
next.  The existing governance (``FlagGuard`` -> adapter) is *not*
bypassed — it lives inside the ``submit_candidate`` tool, so the model
calling that tool still goes through format/score/rate-limit/freeze
checks before anything reaches the platform.

Design borrowed (and trimmed) from lsdefine/GenericAgent's ~100-line
``agent_loop.py``: keep the loop tiny, keep the toolset small, let
``code_run`` be the universal solver instead of pre-registering one
agent per category.
"""
from __future__ import annotations

from .loop import LLMResponse, StepOutcome, ToolCall, run_agent_loop
from .toolbox import TOOLS_SCHEMA, CtfToolBox

__all__ = [
    "LLMResponse",
    "StepOutcome",
    "ToolCall",
    "run_agent_loop",
    "TOOLS_SCHEMA",
    "CtfToolBox",
]
