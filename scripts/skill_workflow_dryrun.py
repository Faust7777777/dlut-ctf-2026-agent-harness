#!/usr/bin/env python3
"""End-to-end dry-run of the skill-race workflow.

Spins up the full pipeline (router → mock agent → flag_guard →
DryRunAdapter → JSONL log → notifications) against 7 fabricated
challenges that exercise every guard branch:

  - misc high-conf       → AUTO_SUBMIT (claims rate slot)
  - forensics high-conf  → HOLD (rate-limit global, claim already taken)
  - web high-conf        → HUMAN_REVIEW (web not in auto_submit_categories)
  - pwn high-conf        → HUMAN_REVIEW (pwn forced)
  - misc low-conf        → HOLD (LOW_CONFIDENCE)
  - misc bad format      → REJECT
  - misc silent agent    → no_candidate

Notifications run in webhook-aware mode: if FEISHU_WEBHOOK is set in
.env they're sent for real, otherwise the call returns
sent=False with a preview body.  Dry-run is therefore safe to run with
or without webhook configured.

After the run, dump the JSONL log path so reviewers can inspect the
event sequence, and print a one-line summary table of decisions.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.common.logging_jsonl import JsonlLogger  # noqa: E402
from ctf_agents.skill.agents.mock import (  # noqa: E402
    make_bad_format_agent,
    make_low_confidence_agent,
    make_mock_agent,
    make_silent_agent,
)
from ctf_agents.skill.router import Challenge  # noqa: E402
from ctf_agents.skill.workflow import SkillWorkflow  # noqa: E402
from ctf_agents.submit.state_store import _atomic_write_json  # noqa: E402


def _wipe_state_for_run(state_path: Path) -> None:
    """Reset rate-limit anchors so the dry-run is reproducible.  We do
    NOT delete the file — production state could exist alongside.  We
    just zero the windows for the demo."""
    if not state_path.exists():
        return
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["global_last_submit_unix"] = 0.0
    payload["global_last_submit_iso"] = None
    for ch in payload.get("challenges", {}).values():
        ch["last_submit_unix"] = 0.0
        ch["last_submit_iso"] = None
    _atomic_write_json(state_path, payload)


def main() -> int:
    cfg_path = PROJECT / "configs" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # Use a separate dryrun state file so we don't pollute the
    # production submission_state.json.
    submit_cfg = dict(cfg.get("submit", {}))
    submit_cfg["state_path"] = "logs/skill_dryrun_state.json"
    cfg["submit"] = submit_cfg

    state_path = PROJECT / submit_cfg["state_path"]
    _wipe_state_for_run(state_path)

    run_id = datetime.now().strftime("skill-dryrun-%Y%m%d-%H%M%S")
    logger = JsonlLogger(logs_dir=str(PROJECT / "logs"), run_id=run_id)

    misc_correct_flag = "flag{mock-misc-correct-flag}"
    forensics_correct_flag = "flag{mock-forensics-correct}"
    web_correct_flag = "flag{mock-web-correct-flag}"
    pwn_correct_flag = "flag{mock-pwn-strong-flag}"

    agents = {
        "misc": make_mock_agent("misc", misc_correct_flag),
        "forensics": make_mock_agent("forensics", forensics_correct_flag),
        "web": make_mock_agent("web", web_correct_flag),
        "pwn": make_mock_agent("pwn", pwn_correct_flag),
        "reverse": make_mock_agent("reverse", "flag{mock-reverse-strong}"),
    }

    workflow = SkillWorkflow(
        project_root=PROJECT, cfg=cfg, agents=agents, logger=logger
    )

    challenges: list[tuple[str, Challenge, dict]] = [
        (
            "misc high-conf → AUTO_SUBMIT (claims rate slot)",
            Challenge(
                id="m-001",
                title="zip 伪加密暗藏 flag",
                category="misc",
                description="zip 文件中藏有 flag，注意伪加密标记",
            ),
            {"override_agent": None},
        ),
        (
            "forensics high-conf → HOLD (rate-limit global)",
            Challenge(
                id="f-001",
                title="pcap 流量取证",
                category="forensics",
                description="分析网络流量中的异常",
            ),
            {"override_agent": None},
        ),
        (
            "web high-conf → HUMAN_REVIEW (category not in auto)",
            Challenge(
                id="w-001",
                title="SSTI 注入题",
                category="web",
                description="Flask SSTI 漏洞利用",
            ),
            {"override_agent": None},
        ),
        (
            "pwn high-conf → HUMAN_REVIEW (pwn forced)",
            Challenge(
                id="p-001",
                title="ret2libc 简单题",
                category="pwn",
                description="栈溢出 + libc 利用",
            ),
            {"override_agent": None},
        ),
        (
            "misc low-conf → HOLD (LOW_CONFIDENCE)",
            Challenge(
                id="m-002",
                title="模糊隐写题",
                category="misc",
                description="无明显证据的隐写题",
            ),
            {"override_agent": make_low_confidence_agent("misc", "flag{shaky-misc}")},
        ),
        (
            "misc bad format → REJECT",
            Challenge(
                id="m-003",
                title="agent 输出错误格式",
                category="misc",
                description="测试 REJECT 路径",
            ),
            {"override_agent": make_bad_format_agent("misc")},
        ),
        (
            "misc silent agent → no_candidate",
            Challenge(
                id="m-004",
                title="agent 无产出",
                category="misc",
                description="测试 no_candidate 路径",
            ),
            {"override_agent": make_silent_agent()},
        ),
    ]

    print(f"=== skill workflow dry-run ===")
    print(f"  config: {cfg_path}")
    print(f"  state:  {state_path}")
    print(f"  log:    logs/{run_id}.jsonl\n")

    summary_rows: list[dict] = []
    for label, ch, opts in challenges:
        if opts["override_agent"] is not None:
            workflow.agents[ch.category] = opts["override_agent"]
        else:
            # restore the default mock for this category
            if ch.category == "misc":
                workflow.agents["misc"] = make_mock_agent("misc", misc_correct_flag)
            elif ch.category == "forensics":
                workflow.agents["forensics"] = make_mock_agent("forensics", forensics_correct_flag)
            elif ch.category == "web":
                workflow.agents["web"] = make_mock_agent("web", web_correct_flag)
            elif ch.category == "pwn":
                workflow.agents["pwn"] = make_mock_agent("pwn", pwn_correct_flag)
        result = workflow.process(ch)
        action = result.get("outcome", "?")
        adapter = result.get("adapter_result")
        adapter_str = ""
        if adapter:
            adapter_str = (
                f"  adapter={'ok' if adapter['ok'] else 'fail'} correct={adapter['correct']}"
            )
        print(f"  [{ch.id}] {label}")
        print(f"    → action={action}{adapter_str}")
        decision_dict = result.get("decision") or {}
        if decision_dict.get("hold_reason"):
            print(f"    hold_reason={decision_dict['hold_reason']}")
        if decision_dict.get("notes"):
            print(f"    notes={decision_dict['notes']}")
        print()
        summary_rows.append(
            {
                "challenge_id": ch.id,
                "category": ch.category,
                "label": label,
                "action": action,
                "adapter_correct": adapter.get("correct") if adapter else None,
                "hold_reason": decision_dict.get("hold_reason"),
                "notes": decision_dict.get("notes", []),
            }
        )

    summary_path = PROJECT / "logs" / f"{run_id}-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "feishu_enabled": cfg.get("feishu", {}).get("enabled", False),
                "feishu_webhook_set": bool(os.environ.get("FEISHU_WEBHOOK", "")),
                "rows": summary_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    actions = {row["action"] for row in summary_rows}
    expected = {"auto_submit", "hold", "human_review", "reject", "no_candidate"}
    missing = expected - actions

    print(f"=== summary ===")
    print(f"  rows: {len(summary_rows)}")
    print(f"  actions seen: {sorted(actions)}")
    if missing:
        print(f"  ⚠ missing actions: {sorted(missing)}")
    else:
        print(f"  ✓ all critical decision branches exercised")
    print(f"  summary written: {summary_path}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
