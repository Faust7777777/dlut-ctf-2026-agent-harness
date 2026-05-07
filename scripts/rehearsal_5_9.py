#!/usr/bin/env python3
"""5/9 dress-rehearsal driver — one command, all guard paths.

Per docs/opus_next_handoff.md §"P0 - 5/9 一键彩排脚本", this script
exercises every contract the production submission path depends on.
Each scenario is independent (own temp project root, own state file)
so the rehearsal can run repeatedly without polluting production state.

Scenarios:

  1. mock workflow paths       — auto_submit / hold / human_review /
                                  reject / no_candidate from one driver
  2. kill switch               — toggling .auto_submit_off downgrades
                                  AUTO_SUBMIT to HUMAN_REVIEW; removing
                                  it restores AUTO_SUBMIT
  3. freeze                    — same challenge wrong twice → frozen,
                                  third decide is HUMAN_REVIEW
  4. force_submit              — frozen state + valid reason →
                                  AUTO_SUBMIT; rate-limit still applies
  5. rate limit                — two high-conf misc submits in a row →
                                  second is HOLD with rate_limit_global
  6. feishu preview            — when webhook missing: preview body;
                                  when --send-feishu and FEISHU_WEBHOOK
                                  set: real send (one health-check msg)
  7. wjx exam dry-run          — optional; if --wjx-url provided, runs
                                  the wjx_exam_mvp.js in dry-run mode
                                  (no submit) to confirm node/playwright
                                  pipeline still works

Exit code is 0 only if every required scenario passed.  Flags
``--scenario NAME`` selects subsets for repeated targeted reruns.

A JSON summary is printed to stdout and a structured event log is
written to ``logs/rehearsal-<ts>.jsonl`` so 5/9 evening can compare
diffs between rehearsal attempts.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

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
from ctf_agents.submit.decisions import Decision, HoldReason, RejectReason  # noqa: E402
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard  # noqa: E402
from ctf_agents.submit.kill_switch import activate, deactivate, is_active  # noqa: E402
from ctf_agents.submit.notifications import (  # noqa: E402
    notify_kill_switch,
    preview_message,
)
from ctf_agents.submit.state_store import (  # noqa: E402
    SubmissionStateStore,
    _atomic_write_json,
)


SUBMIT_CFG_TEMPLATE: dict[str, Any] = {
    "auto_submit": True,
    "auto_submit_categories": ["misc", "forensics"],
    "min_conf_auto_submit": 0.92,
    "min_conf_human_review": 0.70,
    "max_wrong_per_challenge": 2,
    "min_seconds_between_submits_global": 25,
    "min_seconds_between_submits_per_challenge": 90,
    "flag_regex": r"(?i)(flag|dlutctf)\{[^{}\s]{4,128}\}",
    "kill_switch_file": ".auto_submit_off",
    "force_submit_min_reason_length": 10,
    "pwn_reverse_force_human_review": True,
}


@dataclass
class ScenarioResult:
    name: str
    expected: str
    actual: str
    passed: bool
    notes: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    skipped: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "notes": self.notes,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@contextmanager
def temp_project_root() -> Path:
    """Provision an isolated workspace so each scenario starts clean."""
    with tempfile.TemporaryDirectory(prefix="rehearsal_") as td:
        root = Path(td)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        yield root


def _build_cfg(state_path: Path, *, feishu_enabled: bool = False) -> dict:
    submit_cfg = dict(SUBMIT_CFG_TEMPLATE)
    submit_cfg["state_path"] = str(state_path)
    return {"submit": submit_cfg, "feishu": {"enabled": feishu_enabled}}


def _high_conf(challenge_id: str, *, category: str = "misc", flag: Optional[str] = None) -> FlagCandidate:
    f = flag or f"flag{{rehearsal-{challenge_id}-strong}}"
    return FlagCandidate(
        challenge_id=challenge_id,
        flag=f,
        category=category,
        evidence_count=4,
        extraction_confidence=1.0,
        agent_votes=[f] * 3,
        risk="normal",
    )


# -----------------------------------------------------------------------
# Scenarios
# -----------------------------------------------------------------------

def scenario_mock_workflow(logger: JsonlLogger) -> ScenarioResult:
    """Drive the workflow against 5 mock challenges to confirm every
    decision branch fires correctly under one workflow instance."""
    name = "mock_workflow_full_paths"
    expected = "5 actions: auto_submit, hold, human_review, reject, no_candidate"
    t0 = time.perf_counter()
    notes: list[str] = []

    with temp_project_root() as root:
        state_path = root / "logs" / "submission_state.json"
        cfg = _build_cfg(state_path)
        scenario_logger = JsonlLogger(logs_dir=str(root / "logs"), run_id="mock-paths")

        agents = {
            "misc": make_mock_agent("misc", "flag{rehearsal-misc-auto}"),
        }
        wf = SkillWorkflow(project_root=root, cfg=cfg, agents=agents, logger=scenario_logger)

        outcomes: list[str] = []
        outcomes.append(wf.process(Challenge(id="m-auto", title="zip", category="misc"))["outcome"])

        # Switch to a low-confidence agent for the next misc challenge → HOLD
        wf.agents["misc"] = make_low_confidence_agent("misc", "flag{rehearsal-low}")
        outcomes.append(wf.process(Challenge(id="m-low", title="模糊", category="misc"))["outcome"])

        # Bad-format agent → REJECT
        wf.agents["misc"] = make_bad_format_agent("misc")
        outcomes.append(wf.process(Challenge(id="m-bad", title="bad", category="misc"))["outcome"])

        # Silent agent → no_candidate
        wf.agents["misc"] = make_silent_agent()
        outcomes.append(wf.process(Challenge(id="m-silent", title="silent", category="misc"))["outcome"])

        # Web high-conf → HUMAN_REVIEW (category not in auto)
        wf.agents["web"] = make_mock_agent("web", "flag{rehearsal-web}")
        outcomes.append(wf.process(Challenge(id="w-1", title="SSTI", category="web"))["outcome"])

    actual = ",".join(outcomes)
    expected_set = {"auto_submit", "hold", "reject", "no_candidate", "human_review"}
    seen = set(outcomes)
    passed = expected_set.issubset(seen)
    notes.append(f"seen={sorted(seen)}")
    if not passed:
        notes.append(f"missing={sorted(expected_set - seen)}")

    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: {actual}",
        data={"outcomes": outcomes},
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def scenario_kill_switch(logger: JsonlLogger) -> ScenarioResult:
    name = "kill_switch_downgrade_and_recovery"
    expected = "auto→human_review with kill on; auto restored after kill off"
    t0 = time.perf_counter()
    notes: list[str] = []

    with temp_project_root() as root:
        state_path = root / "logs" / "submission_state.json"
        cfg = _build_cfg(state_path)
        guard = FlagGuard(project_root=root, submit_cfg=cfg["submit"])
        ks_path = root / cfg["submit"]["kill_switch_file"]

        # 1) baseline: high-conf misc → AUTO_SUBMIT
        d_baseline = guard.decide(_high_conf("k-1"))
        notes.append(f"baseline={d_baseline.action.value}")

        # 2) flip kill switch ON → high-conf misc downgrades
        activate(ks_path, reason="rehearsal kill switch")
        d_killed = guard.decide(_high_conf("k-2"))
        notes.append(f"after_kill_on={d_killed.action.value}/{d_killed.hold_reason}")

        # 3) flip kill switch OFF → AUTO_SUBMIT path eligible again
        deactivate(ks_path)
        # Note: rate-limit anchor was set by the earlier successful claim,
        # so use a fresh challenge id and reset state to test pure recovery
        snap = guard.state_store.snapshot()
        snap["global_last_submit_unix"] = 0.0
        for ch in snap.get("challenges", {}).values():
            ch["last_submit_unix"] = 0.0
        _atomic_write_json(guard.state_store.state_path, snap)
        d_restored = guard.decide(_high_conf("k-3"))
        notes.append(f"after_kill_off={d_restored.action.value}")

    passed = (
        d_baseline.action is Decision.AUTO_SUBMIT
        and d_killed.action is Decision.HUMAN_REVIEW
        and d_killed.hold_reason is HoldReason.KILL_SWITCH_ACTIVE
        and d_restored.action is Decision.AUTO_SUBMIT
    )
    actual = ",".join(notes)
    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: {actual}",
        data={
            "baseline": d_baseline.to_dict(),
            "killed": d_killed.to_dict(),
            "restored": d_restored.to_dict(),
        },
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def scenario_freeze(logger: JsonlLogger) -> ScenarioResult:
    name = "freeze_after_two_wrong"
    expected = "wrong, wrong, frozen → 3rd decide is HUMAN_REVIEW"
    t0 = time.perf_counter()
    notes: list[str] = []

    with temp_project_root() as root:
        state_path = root / "logs" / "submission_state.json"
        cfg = _build_cfg(state_path)
        guard = FlagGuard(project_root=root, submit_cfg=cfg["submit"])
        cand = _high_conf("freeze-1")

        d1 = guard.decide(cand)
        out1 = guard.record_outcome(cand, d1, correct=False)
        notes.append(f"after_w1=wrong={out1['wrong_count']} frozen={out1['frozen']}")

        # Skip the per-challenge rate window for the rehearsal speed
        snap = guard.state_store.snapshot()
        snap["global_last_submit_unix"] = 0.0
        snap["challenges"][cand.challenge_id]["last_submit_unix"] = 0.0
        _atomic_write_json(guard.state_store.state_path, snap)

        d2 = guard.decide(cand)
        out2 = guard.record_outcome(cand, d2, correct=False)
        notes.append(f"after_w2=wrong={out2['wrong_count']} frozen={out2['frozen']} newly_frozen={out2['newly_frozen']}")

        d3 = guard.decide(cand)
        notes.append(f"after_freeze=action={d3.action.value} frozen_flag={d3.frozen}")

    passed = (
        out1["wrong_count"] == 1
        and out1["frozen"] is False
        and out2["wrong_count"] == 2
        and out2["frozen"] is True
        and out2["newly_frozen"] is True
        and d3.action is Decision.HUMAN_REVIEW
        and d3.frozen
    )
    actual = " | ".join(notes)
    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: {actual}",
        data={"d3": d3.to_dict(), "out1": out1, "out2": out2},
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def scenario_force_submit(logger: JsonlLogger) -> ScenarioResult:
    name = "force_submit_override_on_frozen_with_rate_limit_aftermath"
    expected = "frozen + valid reason → AUTO_SUBMIT; second force still rate-limited"
    t0 = time.perf_counter()
    notes: list[str] = []

    with temp_project_root() as root:
        state_path = root / "logs" / "submission_state.json"
        cfg = _build_cfg(state_path)
        guard = FlagGuard(project_root=root, submit_cfg=cfg["submit"])

        # Seed a frozen state directly
        guard.state_store.force_freeze("force-1", reason="seeded by rehearsal")
        cand = _high_conf("force-1")

        d_no_reason = guard.decide(cand, force_submit=True, force_reason="short")
        notes.append(f"short_reason_action={d_no_reason.action.value} reject={d_no_reason.reject_reason}")

        d_valid = guard.decide(
            cand,
            force_submit=True,
            force_reason="manually verified via browser devtools",
        )
        notes.append(f"override_action={d_valid.action.value} frozen_seen={d_valid.frozen}")

        # The valid override claimed the slot; an immediate second force
        # should be HOLD on rate limit
        d_followup = guard.decide(
            _high_conf("force-2"),
            force_submit=True,
            force_reason="another verified flag right after",
        )
        notes.append(f"followup_action={d_followup.action.value} hold={d_followup.hold_reason}")

    passed = (
        d_no_reason.action is Decision.REJECT
        and "force_submit_reason_too_short" in d_no_reason.notes
        and d_valid.action is Decision.AUTO_SUBMIT
        and d_valid.frozen is True
        and "force_submit_override" in d_valid.notes
        and d_followup.action is Decision.HOLD
        and d_followup.hold_reason is HoldReason.RATE_LIMIT_GLOBAL
    )
    actual = " | ".join(notes)
    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: {actual}",
        data={
            "d_no_reason": d_no_reason.to_dict(),
            "d_valid": d_valid.to_dict(),
            "d_followup": d_followup.to_dict(),
        },
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def scenario_rate_limit(logger: JsonlLogger) -> ScenarioResult:
    name = "rate_limit_blocks_concurrent_auto_submit"
    expected = "first AUTO_SUBMIT claims slot; second decide HOLD rate_limit_global"
    t0 = time.perf_counter()
    notes: list[str] = []

    with temp_project_root() as root:
        state_path = root / "logs" / "submission_state.json"
        cfg = _build_cfg(state_path)
        guard = FlagGuard(project_root=root, submit_cfg=cfg["submit"])

        d_a = guard.decide(_high_conf("rl-A"))
        d_b = guard.decide(_high_conf("rl-B"))
        notes.append(f"a={d_a.action.value} b={d_b.action.value}/{d_b.hold_reason}")

    passed = (
        d_a.action is Decision.AUTO_SUBMIT
        and d_b.action is Decision.HOLD
        and d_b.hold_reason is HoldReason.RATE_LIMIT_GLOBAL
    )
    actual = " | ".join(notes)
    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: {actual}",
        data={"d_a": d_a.to_dict(), "d_b": d_b.to_dict()},
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


def scenario_feishu(logger: JsonlLogger, send_real: bool) -> ScenarioResult:
    name = "feishu_preview_or_send"
    expected = (
        "preview rendered" if not send_real else "real webhook delivered"
    )
    t0 = time.perf_counter()
    notes: list[str] = []
    skipped = False
    skip_reason: Optional[str] = None

    preview = preview_message(
        "kill_switch", activated=True, reason="rehearsal preview"
    )
    notes.append(f"preview_chars={len(preview)}")

    sent_ok = None
    if send_real:
        webhook = os.environ.get("FEISHU_WEBHOOK", "")
        if not webhook:
            skipped = True
            skip_reason = "FEISHU_WEBHOOK env not set"
        else:
            cfg = {"enabled": True}
            outcome = notify_kill_switch(
                cfg,
                activated=True,
                reason="5/9 彩排：测试 webhook，请忽略",
            )
            sent_ok = bool(outcome.get("sent")) and bool(outcome.get("ok"))
            notes.append(f"sent={outcome.get('sent')} ok={outcome.get('ok')}")

    passed = (
        bool(preview)
        and (send_real is False or skipped or sent_ok is True)
    )

    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: send_real={send_real} sent_ok={sent_ok} skipped={skipped}",
        data={
            "preview_chars": len(preview),
            "sent_ok": sent_ok,
            "skipped": skipped,
            "skip_reason": skip_reason,
        },
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=f"preview_chars={len(preview)} sent_ok={sent_ok} skipped={skipped}",
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
        skipped=skipped,
        skip_reason=skip_reason,
    )


def scenario_wjx_dryrun(
    logger: JsonlLogger,
    *,
    url: Optional[str],
    password_env: Optional[str],
    answers_path: Optional[Path],
    lookup_url: Optional[str] = None,
    identity: Optional[str] = None,
) -> ScenarioResult:
    """Run the wjx_exam_assist dry-run (preferred) so 5/9 rehearsal
    exercises the same code that runs on contest day: HTTP lookup,
    SMS / password-used detection, risky-note gating, identity fill.
    Falls back to the MVP only if the assist script is missing.
    """
    name = "wjx_exam_assist_dryrun_optional"
    expected = "node assist dry-run completes (no submit, no auto-click)"
    t0 = time.perf_counter()
    notes: list[str] = []
    skipped = False
    skip_reason: Optional[str] = None

    assist = PROJECT / "scripts" / "wjx_exam_assist.js"
    mvp = PROJECT / "scripts" / "wjx_exam_mvp.js"
    script = assist if assist.exists() else mvp
    if not url:
        skipped = True
        skip_reason = "--wjx-url not provided"
    elif not script.exists():
        skipped = True
        skip_reason = f"neither assist nor mvp present at {assist} / {mvp}"
    elif shutil.which("node") is None:
        skipped = True
        skip_reason = "node binary missing in PATH"

    if skipped:
        return ScenarioResult(
            name=name,
            expected=expected,
            actual="skipped",
            passed=True,
            notes=[f"skip_reason={skip_reason}"],
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            skipped=True,
            skip_reason=skip_reason,
        )

    cmd = ["node", str(script), "--url", url, "--dry-run"]
    if script == assist:
        if lookup_url:
            cmd.extend(["--lookup-url", lookup_url])
        if identity:
            cmd.extend(["--identity", identity])
    if password_env:
        cmd.extend(["--password-env", password_env])
    if answers_path and answers_path.exists():
        cmd.extend(["--answers", str(answers_path)])
    safe_cmd = " ".join(shlex.quote(c) for c in cmd)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, errors="ignore"
        )
        rc = proc.returncode
        notes.append(f"script={script.name} rc={rc}")
        passed = rc == 0
        actual = f"script={script.name} rc={rc} stdout_chars={len(proc.stdout)}"
    except subprocess.TimeoutExpired:
        passed = False
        actual = f"script={script.name} timeout 180s"
        notes.append("timeout")

    logger.event(
        event_type="rehearsal_scenario",
        actor="rehearsal",
        message=f"{name}: cmd={safe_cmd}",
        data={"cmd": safe_cmd, "actual": actual, "script": script.name},
        redact=False,
    )
    return ScenarioResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=passed,
        notes=notes,
        elapsed_ms=(time.perf_counter() - t0) * 1000,
    )


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------

ALL_SCENARIOS: dict[str, Callable[..., ScenarioResult]] = {
    "mock_workflow": scenario_mock_workflow,
    "kill_switch": scenario_kill_switch,
    "freeze": scenario_freeze,
    "force_submit": scenario_force_submit,
    "rate_limit": scenario_rate_limit,
    "feishu": scenario_feishu,
    "wjx": scenario_wjx_dryrun,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="run only the named scenario; repeat for multiple. Default = all.",
    )
    ap.add_argument(
        "--send-feishu",
        action="store_true",
        help="actually send a Feishu test message (requires FEISHU_WEBHOOK in env).",
    )
    ap.add_argument("--wjx-url", default=None)
    ap.add_argument("--wjx-password-env", default=None)
    ap.add_argument(
        "--wjx-answers",
        default=str(PROJECT / "examples" / "dlut_bank_wjx_import_corrected_answers.json"),
    )
    ap.add_argument(
        "--wjx-lookup-url",
        default="http://127.0.0.1:8765/lookup_v2",
        help="HTTP lookup endpoint passed to wjx_exam_assist.js",
    )
    ap.add_argument(
        "--wjx-identity",
        default=None,
        help="JSON identity fields, e.g. '{\"姓名\":\"...\",\"工号\":\"...\"}'",
    )
    args = ap.parse_args()

    selected = args.scenario or list(ALL_SCENARIOS.keys())
    unknown = [s for s in selected if s not in ALL_SCENARIOS]
    if unknown:
        print(f"unknown scenarios: {unknown}", file=sys.stderr)
        return 2

    run_id = datetime.now().strftime("rehearsal-%Y%m%d-%H%M%S")
    logger = JsonlLogger(logs_dir=str(PROJECT / "logs"), run_id=run_id)

    print(f"=== 5/9 dress rehearsal ===")
    print(f"  log: logs/{run_id}.jsonl")
    print(f"  scenarios: {selected}\n")

    results: list[ScenarioResult] = []
    for name in selected:
        fn = ALL_SCENARIOS[name]
        if name == "feishu":
            r = fn(logger, send_real=args.send_feishu)
        elif name == "wjx":
            answers_path = Path(args.wjx_answers) if args.wjx_answers else None
            r = fn(
                logger,
                url=args.wjx_url,
                password_env=args.wjx_password_env,
                answers_path=answers_path,
                lookup_url=args.wjx_lookup_url,
                identity=args.wjx_identity,
            )
        else:
            r = fn(logger)
        results.append(r)
        marker = "PASS" if r.passed and not r.skipped else ("SKIP" if r.skipped else "FAIL")
        print(f"  [{marker}] {r.name}  ({r.elapsed_ms:.1f} ms)")
        for note in r.notes:
            print(f"      {note}")
        if r.skip_reason:
            print(f"      skip_reason: {r.skip_reason}")
        print()

    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenarios_selected": selected,
        "results": [r.to_dict() for r in results],
        "passed": sum(1 for r in results if r.passed and not r.skipped),
        "skipped": sum(1 for r in results if r.skipped),
        "failed": sum(1 for r in results if not r.passed),
    }
    summary_path = PROJECT / "logs" / f"{run_id}-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"=== summary ===")
    print(f"  passed:  {summary['passed']}")
    print(f"  failed:  {summary['failed']}")
    print(f"  skipped: {summary['skipped']}")
    print(f"  summary: {summary_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
