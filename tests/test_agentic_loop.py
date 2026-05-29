"""Agentic loop tests — fully offline (ScriptedClient, DryRunAdapter).

Pins two things that matter:
  1. the happy path drives reason -> code_run -> submit and the submit
     still routes through FlagGuard (AUTO_SUBMIT, not a raw post);
  2. the verify gate refuses a bare guess with no evidence.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ctf_agents.agentic import TOOLS_SCHEMA, CtfToolBox, run_agent_loop
from ctf_agents.agentic.client import ScriptedClient
from ctf_agents.agentic.loop import LLMResponse, ToolCall
from ctf_agents.submit.flag_guard import FlagGuard
from ctf_agents.submit.platform_adapter import DryRunAdapter

FLAG = "flag{unit_test_flag_here}"

SCAN = (
    "import re,pathlib\n"
    "h=[]\n"
    "for p in pathlib.Path('.').rglob('*'):\n"
    "    if p.is_file() and p.name!='found.txt':\n"
    "        try: h+=re.findall(r'(?i)(?:flag|dlutctf)\\{[^{}\\s]{4,128}\\}',p.read_text(errors='ignore'))\n"
    "        except Exception: pass\n"
    "pathlib.Path('evidence').mkdir(exist_ok=True)\n"
    "pathlib.Path('evidence/found.txt').write_text('\\n'.join(h))\n"
    "print(h)\n"
)


def _permissive_cfg(state_path: Path) -> dict:
    return {
        "auto_submit": True,
        "auto_submit_categories": ["misc"],
        "min_conf_auto_submit": 0.0,
        "min_conf_human_review": 0.0,
        "pwn_reverse_force_human_review": False,
        "min_seconds_between_submits_global": 0,
        "min_seconds_between_submits_per_challenge": 0,
        "state_path": str(state_path),
    }


class AgenticLoopTest(unittest.TestCase):
    def _make(self, root: Path):
        sandbox = root / "clean_solve" / "c1"
        sandbox.mkdir(parents=True)
        (sandbox / "a.txt").write_text(f"x {FLAG} y", encoding="utf-8")
        guard = FlagGuard(project_root=root, submit_cfg=_permissive_cfg(root / "logs" / "s.json"))
        toolbox = CtfToolBox(challenge_id="c1", category="misc", sandbox_dir=sandbox,
                             guard=guard, adapter=DryRunAdapter())
        return toolbox

    def test_happy_path_routes_through_guard(self):
        with tempfile.TemporaryDirectory() as d:
            toolbox = self._make(Path(d))
            client = ScriptedClient([
                LLMResponse(tool_calls=[ToolCall("t1", "code_run", {"script": SCAN})]),
                LLMResponse(tool_calls=[ToolCall("t2", "submit_candidate", {
                    "flag": FLAG, "evidence_paths": ["evidence/found.txt"],
                    "reasoning": "regex hit", "confidence": 0.95})]),
            ])
            res = run_agent_loop(client, "sys", "solve c1", toolbox, TOOLS_SCHEMA, max_turns=6)
            self.assertEqual(res["result"], "TASK_DONE")
            self.assertEqual(res["data"]["decision"]["action"], "auto_submit")
            self.assertIsNotNone(res["data"]["submit_result"])

    def test_verify_gate_blocks_evidence_free_guess(self):
        with tempfile.TemporaryDirectory() as d:
            toolbox = self._make(Path(d))
            # Model tries to submit immediately with no evidence -> blocked,
            # then gives up. Loop must not have reached the adapter.
            client = ScriptedClient([
                LLMResponse(tool_calls=[ToolCall("t1", "submit_candidate", {
                    "flag": FLAG, "evidence_paths": [], "reasoning": "guess"})]),
                LLMResponse(tool_calls=[ToolCall("t2", "give_up", {"reason": "no evidence"})]),
            ])
            res = run_agent_loop(client, "sys", "solve c1", toolbox, TOOLS_SCHEMA, max_turns=6)
            self.assertEqual(res["result"], "EXITED")
            self.assertEqual(res["data"]["result"], "NO_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
