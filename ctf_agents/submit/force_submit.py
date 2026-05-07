"""Force-submit override CLI.

Use when the guard has frozen / human-reviewed / held a flag that you
have independently confirmed is correct (e.g., you ran the same
candidate against the platform manually and saw it was accepted, but the
agent path was wrong).  Every invocation requires a non-empty reason of
at least ``force_submit_min_reason_length`` chars; this string lands in
the JSONL log unredacted so the post-game writeup can reconstruct who /
why.

Usage::

    python -m ctf_agents.submit.force_submit \
        --challenge-id web-03 --flag 'flag{verified}' \
        --category web --reason "browser devtools confirmed flag is right; agent missed a 302"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ctf_agents.common.logging_jsonl import JsonlLogger
from .flag_guard import FlagCandidate, FlagGuard
from .decisions import Decision
from .notifications import notify_force_submit_result


def _redact_flag_for_summary(flag: str) -> str:
    if len(flag) <= 14:
        return flag[:6] + "…"
    return flag[:6] + "…" + flag[-4:]


def main() -> int:
    ap = argparse.ArgumentParser(description="Force-submit override CLI")
    ap.add_argument("--challenge-id", required=True)
    ap.add_argument("--flag", required=True)
    ap.add_argument("--category", default="misc")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--config", default="configs/config.yaml")
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Skip the dry-run guard preview and actually call the platform adapter.",
    )
    args = ap.parse_args()

    import yaml  # local import: keep dep optional for tests

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    project_root = Path(args.config).resolve().parent.parent
    submit_cfg = cfg.get("submit", {})
    guard = FlagGuard(project_root=project_root, submit_cfg=submit_cfg)

    cand = FlagCandidate(
        challenge_id=args.challenge_id,
        flag=args.flag,
        category=args.category,
        evidence_count=1,
        extraction_confidence=1.0,
    )
    decision = guard.decide(cand, force_submit=True, force_reason=args.reason)

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "challenge_id": args.challenge_id,
        "category": args.category,
        "flag_redacted": _redact_flag_for_summary(args.flag),
        "reason": args.reason,
        "decision": decision.to_dict(),
        "committed": False,
    }

    logger = JsonlLogger(logs_dir=str(project_root / "logs"))

    if decision.action is not Decision.AUTO_SUBMIT:
        logger.event(
            event_type="force_submit_blocked",
            actor="human:cli",
            challenge_id=args.challenge_id,
            category=args.category,
            message=f"force_submit 被拦截: {decision.reason}",
            data={
                "decision": decision.to_dict(),
                "reason": args.reason,
                "flag_redacted": _redact_flag_for_summary(args.flag),
            },
            redact=False,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 2

    if not args.commit:
        print(json.dumps(
            {**summary, "note": "dry-run preview; rerun with --commit to actually submit"},
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    adapter = _load_adapter(submit_cfg, cfg.get("scope", {}))
    result = adapter.submit_flag(args.challenge_id, args.flag)

    state_update = guard.record_outcome(
        cand,
        decision,
        correct=result.correct,
        platform_response=result.message,
    )
    notification = notify_force_submit_result(
        cfg.get("feishu", {}),
        challenge_id=args.challenge_id,
        flag=args.flag,
        correct=result.correct,
        reason=args.reason,
        actor="human:cli",
    )
    summary["committed"] = True
    summary["adapter_result"] = {
        "ok": result.ok,
        "correct": result.correct,
        "message": result.message[:200],
    }
    summary["state_update"] = state_update
    summary["notification"] = notification

    logger.event(
        event_type="force_submit",
        actor="human:cli",
        challenge_id=args.challenge_id,
        category=args.category,
        message=(
            f"force_submit override 提交：reason={args.reason!r} "
            f"correct={result.correct}"
        ),
        data={
            "flag": args.flag,
            "reason": args.reason,
            "decision": decision.to_dict(),
            "adapter_message": result.message,
            "state_update": state_update,
        },
        redact=False,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.correct else 1


def _load_adapter(submit_cfg: dict, scope_cfg: dict):
    name = submit_cfg.get("adapter", "dryrun")
    if name == "dryrun":
        from .platform_adapter import DryRunAdapter
        return DryRunAdapter()
    if name == "ctfd":
        from .ctfd_adapter import CTFdAdapter
        import os
        return CTFdAdapter(
            base_url=os.environ["CTF_PLATFORM_BASE_URL"],
            token=os.environ["CTF_PLATFORM_TOKEN"],
            scope_cfg=scope_cfg,
        )
    raise NotImplementedError(f"adapter={name} 还没接")


if __name__ == "__main__":
    sys.exit(main())
