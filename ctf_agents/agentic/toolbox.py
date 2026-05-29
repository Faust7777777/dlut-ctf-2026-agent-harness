"""The CTF toolbox — 4 atomic tools, with governance kept *inside* the
submit tool.

    code_run          universal solver (python/bash in the sandbox cwd)
    file_read         read a file under the sandbox
    submit_candidate  verify gate -> FlagGuard -> adapter  (NOT a raw submit)
    give_up           emit NO_CANDIDATE and stop

Key point: ``submit_candidate`` does not put a flag on the platform by
itself.  It runs an in-loop verify gate (evidence must exist), then
``FlagGuard.decide``; only a ``AUTO_SUBMIT`` decision reaches the
adapter, and the outcome is recorded so freeze / rate-limit state still
applies.  Anything else comes back to the model as an observation to
react to (low score, frozen, rate-limited, needs human, ...).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ctf_agents.submit.decisions import Decision
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard
from ctf_agents.submit.platform_adapter import PlatformAdapter, SubmitResult

from .loop import StepOutcome


# Anthropic tool-use schema (name / description / input_schema).  The
# host for this harness is Opus, so we speak its tool format natively.
TOOLS_SCHEMA: list[dict] = [
    {
        "name": "code_run",
        "description": (
            "Run code in the challenge sandbox to inspect the attachment "
            "and derive a flag. Prefer python; bash also allowed. This is "
            "the solver — unzip, strings, binwalk, decode, script, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "bash"]},
                "script": {"type": "string", "description": "Code to execute."},
            },
            "required": ["script"],
        },
    },
    {
        "name": "file_read",
        "description": "Read a UTF-8 (errors-ignored) file under the sandbox.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "submit_candidate",
        "description": (
            "Submit a flag candidate. Goes through a verify gate and "
            "FlagGuard before any platform I/O — you must pass concrete "
            "evidence_paths produced under the sandbox. Returns the guard "
            "decision; react to it (HOLD/HUMAN_REVIEW/rate-limit) rather "
            "than re-submitting blindly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "flag": {"type": "string"},
                "evidence_paths": {"type": "array", "items": {"type": "string"}},
                "reasoning": {"type": "string", "description": "How the flag was derived."},
                "evidence_count": {"type": "integer"},
                "confidence": {"type": "number", "description": "0..1 extraction confidence."},
            },
            "required": ["flag", "evidence_paths", "reasoning"],
        },
    },
    {
        "name": "give_up",
        "description": "Earn a NO_CANDIDATE: stop after the solve path is exhausted.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]


class CtfToolBox:
    def __init__(
        self,
        *,
        challenge_id: str,
        category: str,
        sandbox_dir: str | Path,
        guard: FlagGuard,
        adapter: PlatformAdapter,
        code_timeout_s: float = 60.0,
    ):
        self.challenge_id = challenge_id
        self.category = category
        self.sandbox = Path(sandbox_dir).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.guard = guard
        self.adapter = adapter
        self.code_timeout_s = code_timeout_s

    # --- dispatch -----------------------------------------------------
    def dispatch(self, name: str, args: dict[str, Any]) -> StepOutcome:
        method = getattr(self, f"do_{name}", None)
        if method is None:
            return StepOutcome(data=f"unknown tool: {name}",
                               next_prompt=f"工具 {name!r} 不存在，请用 {[t['name'] for t in TOOLS_SCHEMA]} 之一。")
        return method(args)

    # --- tools --------------------------------------------------------
    def do_code_run(self, args: dict[str, Any]) -> StepOutcome:
        script = args.get("script", "")
        lang = args.get("language", "python")
        if not script.strip():
            return StepOutcome(data="empty script", next_prompt="script 为空，请给出要执行的代码。")
        cmd = ["python3", "-c", script] if lang == "python" else ["bash", "-c", script]
        try:
            proc = subprocess.run(
                cmd, cwd=str(self.sandbox), capture_output=True, text=True,
                errors="ignore", timeout=self.code_timeout_s,
            )
            out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        except subprocess.TimeoutExpired:
            out = f"[timeout after {self.code_timeout_s}s]"
        out = out[:8000]  # keep observations bounded
        return StepOutcome(data={"exit": getattr(proc, "returncode", None) if "proc" in dir() else None,
                                 "output": out},
                           next_prompt="继续：分析输出，若已得到 flag 就用 submit_candidate 提交（带 evidence_paths）。")

    def do_file_read(self, args: dict[str, Any]) -> StepOutcome:
        rel = args.get("path", "")
        target = (self.sandbox / rel).resolve()
        if not str(target).startswith(str(self.sandbox)):
            return StepOutcome(data="path escapes sandbox", next_prompt="只能读取 sandbox 内的文件。")
        if not target.is_file():
            return StepOutcome(data="not a file", next_prompt=f"{rel} 不是可读文件。")
        text = target.read_text(encoding="utf-8", errors="ignore")[:8000]
        return StepOutcome(data={"path": rel, "content": text}, next_prompt="继续分析。")

    def do_submit_candidate(self, args: dict[str, Any]) -> StepOutcome:
        flag = (args.get("flag") or "").strip()
        evidence = args.get("evidence_paths") or []

        # --- in-loop verify gate (the reflection step) ---------------
        # Mirrors codex_validator + GenericAgent's [VERIFY] gate: refuse a
        # bare guess. Every evidence path must actually exist in sandbox.
        missing = [p for p in evidence
                   if not (self.sandbox / p).resolve().is_file()]
        if not evidence or missing:
            return StepOutcome(
                data={"verify": "failed", "missing": missing},
                next_prompt=("未通过验证门：先用 code_run 在 sandbox 内产出真实证据文件，"
                             "再把它们作为 evidence_paths 提交。不要凭空猜 flag。"),
            )

        candidate = FlagCandidate(
            challenge_id=self.challenge_id,
            flag=flag,
            category=self.category,
            evidence_count=int(args.get("evidence_count", len(evidence))),
            extraction_confidence=float(args.get("confidence", 0.8)),
            agent_votes=[flag],
        )

        # --- existing governance, unchanged --------------------------
        decision = self.guard.decide(candidate)
        result: SubmitResult | None = None
        state_update = None
        if decision.action is Decision.AUTO_SUBMIT:
            result = self.adapter.submit_flag(candidate.challenge_id, candidate.flag)
            state_update = self.guard.record_outcome(
                candidate, decision, correct=result.correct,
                platform_response=result.message,
            )

        data = {"decision": decision.to_dict(),
                "submit_result": (None if result is None
                                  else {"ok": result.ok, "correct": result.correct,
                                        "message": result.message[:200]}),
                "state_update": state_update}

        # Terminal: accepted (or dry-run submitted) -> stop the loop.
        if result is not None and result.correct is not False:
            return StepOutcome(data=data, next_prompt=None)
        # Wrong answer -> let the model react and try a different path.
        if result is not None and result.correct is False:
            return StepOutcome(data=data, next_prompt="提交被判错：换思路重解，不要再提交同一个 flag。")
        # Held / human-review / rate-limited -> observation, keep looping.
        return StepOutcome(data=data,
                           next_prompt=f"未自动提交（guard: {decision.action.value} / {decision.reason}）。"
                                       "据此调整：提升证据/置信度，或等限频窗口。")

    def do_give_up(self, args: dict[str, Any]) -> StepOutcome:
        return StepOutcome(data={"result": "NO_CANDIDATE", "reason": args.get("reason", "")},
                           should_exit=True)
