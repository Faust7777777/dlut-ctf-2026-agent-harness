#!/usr/bin/env python3
"""End-to-end offline demo of the agentic loop.

No API key needed: a ScriptedClient plays the model. It shows the real
loop — model calls ``code_run`` to find a flag in the attachment, then
``submit_candidate``, which still goes through the verify gate and
FlagGuard before the (dry-run) adapter.

    python scripts/agentic_solve_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctf_agents.agentic import TOOLS_SCHEMA, CtfToolBox, run_agent_loop
from ctf_agents.agentic.client import ScriptedClient
from ctf_agents.agentic.loop import LLMResponse, ToolCall
from ctf_agents.submit.flag_guard import FlagGuard
from ctf_agents.submit.platform_adapter import DryRunAdapter

FLAG = "flag{r3act_l00p_with_guard}"

SCAN_SCRIPT = r"""
import re, pathlib
pat = re.compile(r"(?i)(?:flag|dlutctf)\{[^{}\s]{4,128}\}")
hits = []
for p in pathlib.Path('.').rglob('*'):
    if p.is_file() and p.name != 'found.txt':
        try: hits += pat.findall(p.read_text(errors='ignore'))
        except Exception: pass
ev = pathlib.Path('evidence'); ev.mkdir(exist_ok=True)
(ev / 'found.txt').write_text('\n'.join(hits), encoding='utf-8')
print('FOUND:', hits)
"""


def _scripted_model() -> ScriptedClient:
    return ScriptedClient([
        # Turn 1: reason -> run the solver
        LLMResponse(
            content="先扫一遍附件里的 flag 串。",
            tool_calls=[ToolCall(id="t1", name="code_run",
                                 arguments={"language": "python", "script": SCAN_SCRIPT})],
        ),
        # Turn 2: observed the flag -> submit with evidence
        LLMResponse(
            content="拿到 flag，带证据提交。",
            tool_calls=[ToolCall(id="t2", name="submit_candidate", arguments={
                "flag": FLAG,
                "evidence_paths": ["evidence/found.txt"],
                "reasoning": "strings/regex 在附件中直接命中 flag 串。",
                "evidence_count": 2,
                "confidence": 0.95,
            })],
        ),
    ])


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="agentic_demo_"))
    sandbox = root / "clean_solve" / "demo1"
    sandbox.mkdir(parents=True)
    (sandbox / "chall.txt").write_text(f"junk junk\nhidden here -> {FLAG}\nmore junk", encoding="utf-8")

    submit_cfg = {
        "auto_submit": True,
        "auto_submit_categories": ["misc"],
        "min_conf_auto_submit": 0.0,
        "min_conf_human_review": 0.0,
        "pwn_reverse_force_human_review": False,
        "min_seconds_between_submits_global": 0,
        "min_seconds_between_submits_per_challenge": 0,
        "state_path": "logs/submission_state.json",
    }
    guard = FlagGuard(project_root=root, submit_cfg=submit_cfg)
    toolbox = CtfToolBox(challenge_id="demo1", category="misc", sandbox_dir=sandbox,
                         guard=guard, adapter=DryRunAdapter())

    def trace(ev: dict) -> None:
        if ev["event"] == "tool_call":
            print(f"  🛠️  {ev['name']}({list(ev['args'])})")
        elif ev["event"] == "tool_result":
            print(f"     ↳ {str(ev['data'])[:160]}")

    print("=== agentic loop (offline, scripted model) ===")
    result = run_agent_loop(
        client=_scripted_model(),
        system_prompt="你是 CTF 求解 agent。用工具解题，提交必须带证据。",
        user_input="题目 demo1（misc）。附件在 sandbox 里，求 flag。",
        toolbox=toolbox, tools_schema=TOOLS_SCHEMA, max_turns=10, trace=trace,
    )
    print(f"\n=== loop result: {result['result']} (turns={result.get('turns')}) ===")
    print(f"submit decision: {result.get('data', {}).get('decision', {}).get('action')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
