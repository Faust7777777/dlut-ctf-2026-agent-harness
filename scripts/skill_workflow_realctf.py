#!/usr/bin/env python3
"""End-to-end run of the skill workflow on real BJDCTF 2020 Misc files.

Files come from the official ``BjdsecCA/BJDCTF2020_January`` GitHub
repo (the same source BUUCTF imported as [BJDCTF 2020]).  We run the
deterministic ``real_misc_agent`` over each one and feed the candidates
through the same FlagGuard / DryRunAdapter / JSONL pipeline used by the
mock dry-run.  This proves the pipeline works on real CTF data — not
just synthesized fixtures.

The ``flag_regex`` in the project config is overridden for this run to
match BJDCTF's flag format (BJD{...}, NSSCTF{...}, etc.), since the
production setting is tuned for the 5/10 contest's expected format.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.common.logging_jsonl import JsonlLogger  # noqa: E402
from ctf_agents.skill.agents.misc_real import real_misc_agent  # noqa: E402
from ctf_agents.skill.router import Challenge  # noqa: E402
from ctf_agents.skill.workflow import SkillWorkflow  # noqa: E402
from ctf_agents.submit.state_store import _atomic_write_json  # noqa: E402


CHALLENGES_DIR = PROJECT / "data" / "external_ctf" / "bjdctf2020-misc"

CHALLENGES = [
    ("misc-bjd-签到",
     "BJDCTF 2020 [Misc] 签个到",
     "签个到.zip",
     "warmup zip with embedded misdirection-typed inner file"),
    ("misc-bjd-猜",
     "BJDCTF 2020 [Misc] 你猜我是个啥",
     "你猜我是个啥.zip",
     ".zip extension lying about real PNG content"),
    ("misc-bjd-藏",
     "BJDCTF 2020 [Misc] 藏藏藏",
     "藏藏藏.rar",
     "nested archives with hidden flag"),
    ("misc-bjd-认真",
     "BJDCTF 2020 [Misc] 认真你就输了",
     "认真你就输了.rar",
     "embedded file in document/archive"),
]


def main() -> int:
    cfg_path = PROJECT / "configs" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    submit_cfg = dict(cfg.get("submit", {}))
    # Use a separate dryrun state file so we don't pollute production
    submit_cfg["state_path"] = "logs/skill_realctf_state.json"
    # Broader flag regex to match BJDCTF/HCTF/NSSCTF formats commonly
    # appearing in the source bank
    submit_cfg["flag_regex"] = r"(?i)(?:flag|bjd|hctf|dlutctf|dasctf|nss|moectf)\{[^{}\s]{3,200}\}"
    cfg["submit"] = submit_cfg

    state_path = PROJECT / submit_cfg["state_path"]
    if state_path.exists():
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["global_last_submit_unix"] = 0.0
        for ch in payload.get("challenges", {}).values():
            ch["last_submit_unix"] = 0.0
        _atomic_write_json(state_path, payload)

    if not CHALLENGES_DIR.exists():
        print(f"missing {CHALLENGES_DIR}", file=sys.stderr)
        print("did you run the BJDCTF download step?", file=sys.stderr)
        return 2

    run_id = datetime.now().strftime("skill-realctf-%Y%m%d-%H%M%S")
    logger = JsonlLogger(logs_dir=str(PROJECT / "logs"), run_id=run_id)

    workflow = SkillWorkflow(
        project_root=PROJECT,
        cfg=cfg,
        agents={"misc": real_misc_agent},
        logger=logger,
    )

    print(f"=== skill workflow REAL CTF dry-run ===")
    print(f"  source: BjdsecCA/BJDCTF2020_January (Misc)")
    print(f"  state:  {state_path}")
    print(f"  log:    logs/{run_id}.jsonl")
    print(f"  flag_regex: {submit_cfg['flag_regex']}\n")

    summary_rows = []
    for cid, title, fname, hint in CHALLENGES:
        attachment = CHALLENGES_DIR / fname
        ch = Challenge(
            id=cid,
            title=title,
            category="misc",
            description=hint,
            attachments=[str(attachment)],
        )
        result = workflow.process(ch)
        action = result["outcome"]
        decision = result.get("decision") or {}
        flag_in_decision = decision.get("flag", "") if decision else ""
        flag_redacted = ""
        if flag_in_decision:
            if len(flag_in_decision) <= 14:
                flag_redacted = flag_in_decision[:6] + "…"
            else:
                flag_redacted = flag_in_decision[:6] + "…" + flag_in_decision[-4:]

        print(f"[{cid}] {title}")
        print(f"  attachment: {fname} ({attachment.stat().st_size} B)")
        print(f"  → action={action}", end="")
        if decision.get("score"):
            print(f"  score={decision['score']:.2f}", end="")
        if flag_redacted:
            print(f"  flag={flag_redacted}", end="")
        print()
        if decision.get("hold_reason"):
            print(f"    hold_reason={decision['hold_reason']}")
        if decision.get("notes"):
            print(f"    notes={decision['notes']}")
        adapter = result.get("adapter_result")
        if adapter:
            print(f"    adapter: ok={adapter['ok']} correct={adapter['correct']}")
        print()
        summary_rows.append({
            "challenge_id": cid,
            "title": title,
            "attachment": fname,
            "action": action,
            "score": decision.get("score"),
            "hold_reason": decision.get("hold_reason"),
            "flag_redacted": flag_redacted or None,
            "adapter_correct": adapter.get("correct") if adapter else None,
        })

    summary_path = PROJECT / "logs" / f"{run_id}-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source": "BjdsecCA/BJDCTF2020_January Misc",
                "rows": summary_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"=== summary ===")
    print(f"  challenges: {len(summary_rows)}")
    actions = Counter(r["action"] for r in summary_rows) if False else None  # noqa
    from collections import Counter as _Counter
    counter = _Counter(r["action"] for r in summary_rows)
    for action, n in sorted(counter.items()):
        print(f"  {action}: {n}")
    print(f"  summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
