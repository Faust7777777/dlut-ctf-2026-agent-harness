"""The ReAct loop — provider-neutral, ~80 lines.

A client only has to implement::

    chat(messages, tools) -> LLMResponse

where ``messages`` is a list of neutral dicts (see below) and ``tools``
is the JSON tool schema.  The loop never talks to any SDK directly, so
the same loop runs against a real model (``AnthropicClient``) or a
canned script (``ScriptedClient``) for offline tests / dry-run.

Neutral message shapes::

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall, ...]}
    {"role": "tool", "tool_results": [{"tool_use_id": str, "content": str}]}

Stop conditions (same vocabulary as GenericAgent):
    - a tool returns ``should_exit=True``            -> EXITED
    - a tool returns ``next_prompt=None``            -> TASK_DONE
    - model emits no tool call                       -> DONE_NO_TOOL
    - ``max_turns`` reached                          -> MAX_TURNS
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class StepOutcome:
    """What a tool hands back to the loop.

    ``next_prompt`` is the observation fed to the model next turn.  Set
    it to ``None`` to declare the task finished; set ``should_exit`` for
    a hard stop (e.g. give_up / NO_CANDIDATE).
    """
    data: Any = None
    next_prompt: Optional[str] = None
    should_exit: bool = False


class LLMClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


def run_agent_loop(
    client: LLMClient,
    system_prompt: str,
    user_input: str,
    toolbox: Any,
    tools_schema: list[dict],
    *,
    max_turns: int = 20,
    trace: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Drive the model until a stop condition fires. Returns a summary."""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    _emit = trace or (lambda ev: None)

    for turn in range(1, max_turns + 1):
        resp = client.chat(messages, tools_schema)
        _emit({"event": "llm_turn", "turn": turn, "content": resp.content,
               "tool_calls": [tc.name for tc in resp.tool_calls]})
        messages.append({"role": "assistant", "content": resp.content,
                         "tool_calls": resp.tool_calls})

        if not resp.tool_calls:
            # No action requested -> treat the text as the final answer.
            return {"result": "DONE_NO_TOOL", "turns": turn, "content": resp.content}

        tool_results: list[dict] = []
        for tc in resp.tool_calls:
            _emit({"event": "tool_call", "turn": turn, "name": tc.name, "args": tc.arguments})
            outcome = toolbox.dispatch(tc.name, tc.arguments)
            _emit({"event": "tool_result", "turn": turn, "name": tc.name,
                   "data": outcome.data, "next_prompt": outcome.next_prompt,
                   "should_exit": outcome.should_exit})

            if outcome.should_exit:
                return {"result": "EXITED", "turns": turn, "data": outcome.data}
            if outcome.next_prompt is None:
                return {"result": "TASK_DONE", "turns": turn, "data": outcome.data}

            content = (outcome.data if isinstance(outcome.data, str)
                       else json.dumps(outcome.data, ensure_ascii=False, default=str))
            tool_results.append({"tool_use_id": tc.id, "content": content})

        # Feed observations back; the next turn's user message carries
        # the tool results plus the latest follow-up prompt. (At least one
        # tool ran, and none asked to stop, so outcome.next_prompt is set.)
        messages.append({"role": "tool", "tool_results": tool_results})
        messages.append({"role": "user", "content": outcome.next_prompt})

    return {"result": "MAX_TURNS", "turns": max_turns}
