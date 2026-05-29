"""LLM clients for the loop.

``ScriptedClient`` returns canned responses turn-by-turn so the loop runs
offline (dry-run, CI, tests).  ``AnthropicClient`` is the real adapter: it
translates the neutral message list into the Anthropic Messages tool-use
format and back.  Both expose the same ``chat(messages, tools)`` surface,
so the loop never knows which one it is driving.
"""
from __future__ import annotations

from typing import Any, Callable

from .loop import LLMResponse, ToolCall


class ScriptedClient:
    """Replays a fixed list of LLMResponses (one per turn).

    Each step may be an ``LLMResponse`` or a zero-arg callable returning
    one (so a step can inspect state before responding).
    """

    def __init__(self, steps: list[Any]):
        self._steps = list(steps)
        self._i = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if self._i >= len(self._steps):
            return LLMResponse(content="(script exhausted)", tool_calls=[])
        step = self._steps[self._i]
        self._i += 1
        return step() if callable(step) else step


class AnthropicClient:
    """Real client. Requires ``anthropic`` installed and an API key.

    Kept deliberately thin; the offline path above is the tested one.
    """

    def __init__(self, model: str = "claude-opus-4-8", max_tokens: int = 4096, **kw):
        import anthropic  # late import; optional dependency

        self._client = anthropic.Anthropic(**kw)
        self.model = model
        self.max_tokens = max_tokens

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        system, api_messages = self._to_anthropic(messages)
        resp = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            system=system, messages=api_messages, tools=tools,
        )
        text, calls = "", []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return LLMResponse(content=text, tool_calls=calls)

    @staticmethod
    def _to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
        system = ""
        out: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                system = m["content"]
            elif role == "assistant":
                blocks: list[dict] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc.id,
                                   "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]}
                    for r in m["tool_results"]
                ]})
            else:  # user
                if m.get("content"):
                    out.append({"role": "user", "content": m["content"]})
        return system, out
