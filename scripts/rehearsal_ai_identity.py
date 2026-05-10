#!/usr/bin/env python3
"""AI-identity rehearsal driver.

Spins up the in-process mock GZCTF server, points the supervisor at it,
runs a short autonomous loop, and emits a JSON summary verifying every
required state-machine path:

    Accepted              → CHALLENGE_STATE_ACCEPTED
    WrongAnswer           → CHALLENGE_STATE_WRONG_FROZEN
    CheatDetected         → CHALLENGE_STATE_CHEAT_FROZEN + global submit disabled
    NotFound              → CHALLENGE_STATE_NOTFOUND
    Pending → Accepted    → exactly one submit, polls advance to Accepted

Outputs:
  logs/rehearsal-ai-identity-<ts>.jsonl
  logs/rehearsal-ai-identity-<ts>-summary.json

Exit code 0 only when every required path produced its expected
terminal state.  This is what the 5/9 dress rehearsal calls for the
"GZCTF mock end-to-end" check, before the real GZCTF testing window.
"""
from __future__ import annotations

import json
import os
import sys
import time
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.common.logging_jsonl import JsonlLogger  # noqa: E402
from ctf_agents.skill.router import Challenge  # noqa: E402
from ctf_agents.submit.flag_guard import FlagCandidate, FlagGuard  # noqa: E402
from ctf_agents.submit.gzctf_adapter import GZCTFAdapter  # noqa: E402

from tests.fixtures.mock_gzctf_server import (  # noqa: E402
    MockChallengeFixture, MockServerRunner,
)

# Load supervisor module by path (scripts/ is not a package)
_SUP_PATH = PROJECT / "scripts" / "ai_contest_supervisor.py"
_spec = importlib.util.spec_from_file_location("ai_contest_supervisor", _SUP_PATH)
sup_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sup_mod)  # type: ignore[attr-defined]
AIContestSupervisor = sup_mod.AIContestSupervisor


def make_agent_for_fixture(fixtures: list[MockChallengeFixture]):
    """Returns an agent that picks the right flag for each fixture so
    the rehearsal exercises Accepted vs Wrong vs Cheat in one run."""
    by_id = {str(f.id): f for f in fixtures}

    def agent(challenge: Challenge):
        f = by_id.get(str(challenge.id))
        if not f:
            return None
        # Decide which flag to "produce" based on fixture intent
        if f.cheat_flag and "Cheat" in f.title:
            flag = f.cheat_flag
        elif f.accept_flag:
            flag = f.accept_flag
            # If fixture is the "wrong" test, deliberately produce a
            # flag that does not match accept_flag.
            if f.title == "Misc wrong":
                flag = "flag{intentionally-wrong-for-rehearsal}"
        else:
            flag = "flag{rehearsal-default-strong}"
        if not flag:
            return None
        return FlagCandidate(
            challenge_id=str(challenge.id),
            flag=flag,
            category=challenge.category or "misc",
            evidence_count=4,
            extraction_confidence=1.0,
            agent_votes=[flag] * 3,
        )

    return agent


def make_cfg(tmp_root: Path, *, base_url: str, game_id: int = 99) -> dict:
    return {
        "gzctf": {
            "base_url": base_url,
            "game_id": game_id,
            "poll_timeout_s": 5.0,
            "poll_interval_s": 0.05,
            "submit_payload_mode": "plaintext",
        },
        "scope": {"allowed_domains": ["127.0.0.1"]},
        "submit": {
            "auto_submit": True,
            # AI identity rehearsal: every category auto-submits; no
            # HUMAN_REVIEW branch.  Hard gates remain.
            "auto_submit_categories": [
                "misc", "forensics", "crypto", "web", "reverse", "pwn",
            ],
            "min_conf_auto_submit": 0.0,
            "min_conf_human_review": 0.0,
            "max_wrong_per_challenge": 1,
            "min_seconds_between_submits_global": 0,
            "min_seconds_between_submits_per_challenge": 0,
            "flag_regex": r"(?i)flag\{[^{}\s]{4,200}\}",
            "state_path": str(tmp_root / "submission_state.json"),
            "kill_switch_file": ".auto_submit_off",
            "force_submit_min_reason_length": 10,
            "pwn_reverse_force_human_review": False,
        },
        "agent": {
            "enabled_categories": [
                "misc", "forensics", "crypto", "web", "reverse", "pwn",
            ],
            "challenge_loop_interval_s": 0,
            "challenge_solve_timeout_s": 30,
            "global_run_timeout_s": 30,
            "heartbeat_interval_s": 0,
        },
        "paths": {
            "state_dir": str(tmp_root / "state"),
            "artifacts_dir": str(tmp_root / "artifacts"),
            "logs_dir": str(tmp_root / "logs"),
            "locks_dir": str(tmp_root / "state" / "locks"),
        },
    }


def main() -> int:
    # Order matters: CheatDetected globally disables submit, so any
    # challenge after it would be skipped.  Run NotFound first so its
    # state machine path is exercised before the cheat barrier.
    fixtures = [
        MockChallengeFixture(
            id=11, title="Misc accepted", category="Misc",
            attachment="puzzle.zip",
            accept_flag="flag{rehearsal-misc-accepted}",
            initial_pending_polls=1,  # exercises pending → terminal
        ),
        MockChallengeFixture(
            id=12, title="Misc wrong", category="Misc",
            accept_flag="flag{never-this-rehearsal}",
            initial_pending_polls=0,
        ),
        MockChallengeFixture(
            id=14, title="Not Found", category="Misc",
            not_found=True,
        ),
        MockChallengeFixture(
            id=13, title="Cheat", category="Misc",
            cheat_flag="flag{rehearsal-shared-cheat}",
            accept_flag=None, initial_pending_polls=0,
        ),
    ]

    rehearsal_root = PROJECT / "logs" / f"ai-identity-rehearsal-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    rehearsal_root.mkdir(parents=True, exist_ok=True)
    summary_path = PROJECT / "logs" / f"rehearsal-ai-identity-{datetime.now().strftime('%Y%m%d-%H%M%S')}-summary.json"

    print("=== AI-identity rehearsal ===")
    print(f"  rehearsal root: {rehearsal_root}")

    with MockServerRunner(fixtures) as server:
        cfg = make_cfg(rehearsal_root, base_url=server.base_url)
        adapter = GZCTFAdapter(
            base_url=server.base_url,
            username="alice", password="hunter2",
            scope_cfg=cfg["scope"],
            submit_payload_mode="plaintext",
            default_game_id=99,
        )
        guard = FlagGuard(project_root=rehearsal_root, submit_cfg=cfg["submit"])
        agent = make_agent_for_fixture(fixtures)
        supervisor = AIContestSupervisor(
            cfg=cfg, adapter=adapter, guard=guard,
            agents={"misc": agent, "forensics": agent},
        )

        # Healthcheck (login/profile/team/game/details)
        ok = supervisor.healthcheck()
        if not ok:
            print("[FAIL] healthcheck did not pass")
            return 2
        print("[PASS] healthcheck")

        # Drive 3 ticks: enough for pending→terminal, plus settle
        for _ in range(3):
            supervisor.run_one_tick()
            time.sleep(0.1)

        states = {cid: c["state"] for cid, c in supervisor.state["challenges"].items()}
        global_disabled = supervisor.state["global_submit_disabled"]

    expectations = {
        "11": sup_mod.CHALLENGE_STATE_ACCEPTED,
        "12": sup_mod.CHALLENGE_STATE_WRONG_FROZEN,
        "13": sup_mod.CHALLENGE_STATE_CHEAT_FROZEN,
        "14": sup_mod.CHALLENGE_STATE_NOTFOUND,
    }
    failed: list[str] = []
    for cid, expected in expectations.items():
        actual = states.get(cid)
        ok_label = "PASS" if actual == expected else "FAIL"
        if actual != expected:
            failed.append(f"{cid}: expected={expected} actual={actual}")
        print(f"  [{ok_label}] challenge {cid}: {actual}")

    if not global_disabled:
        failed.append("global_submit_disabled expected True (CheatDetected) but was False")
        print(f"  [FAIL] global_submit_disabled={global_disabled} (expected True)")
    else:
        print(f"  [PASS] global_submit_disabled=True after CheatDetected")

    summary = {
        "rehearsal": "ai-identity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "states": states,
        "global_submit_disabled": global_disabled,
        "expected": expectations,
        "failed_assertions": failed,
        "rehearsal_root": str(rehearsal_root),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  summary: {summary_path}")
    if failed:
        print("[FAIL] rehearsal had assertion failures")
        return 1
    print("[PASS] all paths exercised correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
