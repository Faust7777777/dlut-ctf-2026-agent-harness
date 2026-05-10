#!/usr/bin/env python3
"""Real-LLM solving E2E for challenges 6, 8, 10.

Per the operator's "real LLM solving" handoff: do NOT pre-stage
expected_flag-derived candidates.  The candidate JSON files were
authored live by Claude Code reasoning over only the attachment
contents; this script just exercises the platform path:

    pre-staged artifacts (cc_*.md + codex_candidates.json)
    -> supervisor.run_one_tick
    -> codex sidecar ingest (validator + path policy + existence)
    -> FlagGuard.decide
    -> GZCTFAdapter.submit_flag_for_game on local 127.0.0.1
    -> Accepted / WrongAnswer

For the 5 unrelated cids in the same game (7, 9, 11, 12, 13) we set
``enabled_categories = []`` so the internal misc agent does NOT
accidentally grep their attachments and submit a flag — that would
contaminate the "real LLM solving" measurement.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
import bootstrap_rehearsal as boot  # noqa: E402

_SUP_PATH = PROJECT / "scripts" / "ai_contest_supervisor.py"
_spec = importlib.util.spec_from_file_location("ai_contest_supervisor", _SUP_PATH)
sup_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sup_mod)  # type: ignore[attr-defined]
AIContestSupervisor = sup_mod.AIContestSupervisor

from ctf_agents.submit.flag_guard import FlagGuard  # noqa: E402
from ctf_agents.submit.gzctf_adapter import GZCTFAdapter  # noqa: E402

TARGET_CIDS = {"6", "7", "8", "9", "10", "11"}

LAB_FLAG_RE = r"(?i)[a-z][a-z0-9_-]*\{[^{}]{4,400}\}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-LLM solving E2E harness")
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="keep state/local-gzctf-real-llm and verify duplicate/resume behavior",
    )
    return parser.parse_args(argv)


def load_env() -> None:
    env_path = LAB / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def assert_artifacts_exist() -> dict:
    """Confirm the live-authored artifact files are in place for each
    target cid before we hand the supervisor the keys.  Returns a
    per-cid map of {expected_files_present: bool}.  Bail if any
    target is missing the canonical 5 deliverables."""
    expected = {
        "cc_hypothesis.md",
        "subagent_request.md",
        "subagent_reply.md",
        "cc_final_decision.md",
        "codex_candidates.json",
    }
    out = {}
    for cid in sorted(TARGET_CIDS):
        d = PROJECT / "artifacts" / "challenges" / cid
        present = {p.name for p in d.iterdir() if p.is_file()}
        missing = expected - present
        out[cid] = {
            "dir": str(d.relative_to(PROJECT)),
            "present": sorted(expected & present),
            "missing": sorted(missing),
        }
    bad = [cid for cid, v in out.items() if v["missing"]]
    if bad:
        raise SystemExit(f"missing live-authored artifacts for cids: {bad}; check {out}")
    return out


def build_cfg(game_id: int) -> dict:
    return {
        "project": {"name": "real-llm-solve", "timezone": "Asia/Shanghai"},
        "gzctf": {
            "base_url": boot.BASE_URL,
            "game_id": game_id,
            "auth_mode": "password",
            "username_env": "GZCTF_USERNAME",
            "password_env": "GZCTF_PASSWORD",
            "submit_payload_mode": "plaintext",
            "poll_timeout_s": 30.0,
            "poll_interval_s": 1.0,
        },
        "scope": {
            "allowed_domains": ["127.0.0.1", "localhost", "files"],
            "url_rewrites": {"http://files:8081": "http://127.0.0.1:8081"},
            "deny_public_scan": True,
        },
        "submit": {
            "auto_submit": True,
            # Widen so a Web/Crypto/Reverse codex hit is allowed to
            # auto-submit, but keep pwn/reverse force_human off so a
            # high-confidence Codex Reverse candidate (cid 10) can
            # also fire end-to-end.
            "auto_submit_categories": ["misc", "forensics", "crypto", "web", "reverse", "pwn"],
            "min_conf_auto_submit": 0.60,
            "min_conf_human_review": 0.45,
            "max_wrong_per_challenge": 1,
            "min_seconds_between_submits_global": 0,
            "min_seconds_between_submits_per_challenge": 0,
            "flag_regex": LAB_FLAG_RE,
            "state_path": "state/local-gzctf-real-llm/submission_state.json",
            "kill_switch_file": ".auto_submit_off",
            "force_submit_min_reason_length": 10,
            "pwn_reverse_force_human_review": False,
        },
        "agent": {
            # Empty: the internal misc agent must NOT silently solve
            # for any cid this run, so the result table reflects ONLY
            # what the live LLM reasoning produced.
            "enabled_categories": [],
            "challenge_loop_interval_s": 0,
            "challenge_solve_timeout_s": 60,
            "global_run_timeout_s": 60,
            "heartbeat_interval_s": 0,
        },
        "paths": {
            "state_dir": "state/local-gzctf-real-llm",
            "artifacts_dir": "artifacts",
            "logs_dir": "logs/local-gzctf-real-llm",
            "locks_dir": "state/local-gzctf-real-llm/locks",
        },
        "feishu": {"enabled": True, "notify_accepted": True, "mention_user_ids": []},
        "codex_sidecar": {"enabled": True},
    }


def build_supervisor(cfg: dict) -> AIContestSupervisor:
    gz = cfg["gzctf"]
    adapter = GZCTFAdapter(
        base_url=gz["base_url"],
        username=os.environ.get(gz["username_env"]),
        password=os.environ.get(gz["password_env"]),
        cookie_jar_path=None,
        scope_cfg=cfg.get("scope") or {},
        submit_payload_mode=gz.get("submit_payload_mode", "plaintext"),
        api_public_key=None,
        default_game_id=gz.get("game_id"),
        auth_mode="password",
    )
    guard = FlagGuard(project_root=PROJECT, submit_cfg=cfg["submit"])
    return AIContestSupervisor(cfg=cfg, adapter=adapter, guard=guard)


def reset_state_root(state_root: Path) -> None:
    if not state_root.exists():
        return
    for path in sorted(state_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() or path.is_symlink():
            path.unlink()


def snapshot_submit_counts(sup: AIContestSupervisor) -> dict[str, int]:
    snapshot = sup.guard.state_store.snapshot()
    challenges = snapshot.get("challenges") or {}
    counts = {}
    for cid in sorted(TARGET_CIDS):
        submits = (challenges.get(cid) or {}).get("submits") or []
        counts[cid] = len(submits)
    return counts


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env()
    boot.assert_local_base_url()
    boot.wait_ready()

    # We assume the e2e bundle import already happened (see
    # run_public_bundles_e2e.py).  Just look up the existing game.
    admin = boot.login(boot.ADMIN_USERNAME, boot.ADMIN_PASSWORD)
    game = boot.ensure_game(admin)
    game_id = int(game["id"])

    artifact_inventory = assert_artifacts_exist()

    # Reset state so this run is reproducible.
    state_root = PROJECT / "state" / "local-gzctf-real-llm"
    if not args.no_reset:
        reset_state_root(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "locks").mkdir(parents=True, exist_ok=True)

    cfg = build_cfg(game_id)
    sup = build_supervisor(cfg)
    if not sup.healthcheck():
        print("[fatal] healthcheck failed", file=sys.stderr)
        return 3

    submit_counts_before = snapshot_submit_counts(sup)
    sup.run_one_tick()
    sup.run_one_tick()  # absorb any pending poll loops
    submit_counts_after = snapshot_submit_counts(sup)
    submit_count_deltas = {
        cid: submit_counts_after[cid] - submit_counts_before[cid]
        for cid in sorted(TARGET_CIDS)
    }
    if args.no_reset:
        violations = {cid: delta for cid, delta in submit_count_deltas.items() if delta != 0}
        if violations:
            raise SystemExit(f"no-reset run created new submits: {violations}")

    # Collect outcomes per target cid only
    rows = []
    for cid in sorted(TARGET_CIDS):
        cstate = sup.state["challenges"].get(cid, {})
        guard_snap = sup.guard.state_store.snapshot()["challenges"].get(cid, {})
        submits = guard_snap.get("submits") or []
        last = submits[-1] if submits else {}
        rows.append({
            "challenge_id": cid,
            "supervisor_state": cstate.get("state"),
            "submit_count": len(submits),
            "submit_count_before": submit_counts_before[cid],
            "submit_count_after": submit_counts_after[cid],
            "submit_count_delta": submit_count_deltas[cid],
            "last_status": last.get("platform_response"),
            "last_correct": last.get("correct"),
            "category": cstate.get("category"),
            "title": cstate.get("title"),
        })

    # Plus a flag-leak scan against the big logs / state files
    expected_flags_authored_in_artifacts = []
    for cid in sorted(TARGET_CIDS):
        cj = PROJECT / "artifacts" / "challenges" / cid / "codex_candidates.json"
        try:
            arr = json.loads(cj.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            arr = []
        for entry in arr:
            cand = entry.get("candidate")
            if cand:
                expected_flags_authored_in_artifacts.append((cid, cand))

    leaks = []
    leak_scan_targets = [sup.logger.path]
    for fn in (state_root / "ai_contest_state.json", state_root / "submission_state.json"):
        if fn.exists():
            leak_scan_targets.append(fn)
    for tgt in leak_scan_targets:
        try:
            text = tgt.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for cid, fl in expected_flags_authored_in_artifacts:
            if fl in text:
                leaks.append({"file": str(tgt.relative_to(PROJECT)), "cid": cid})

    summary = {
        "base_url": boot.BASE_URL,
        "game_id": game_id,
        "target_cids": sorted(TARGET_CIDS),
        "resume_mode": bool(args.no_reset),
        "state_reset_performed": not args.no_reset,
        "artifact_inventory": artifact_inventory,
        "submit_counts_before": submit_counts_before,
        "submit_counts_after": submit_counts_after,
        "submit_count_deltas": submit_count_deltas,
        "rows": rows,
        "candidates_authored_count": len(expected_flags_authored_in_artifacts),
        "leak_findings": leaks,
        "supervisor_log": str(sup.logger.path.relative_to(PROJECT)),
        "state_path": str((state_root / "ai_contest_state.json").relative_to(PROJECT)),
    }
    out = PROJECT / "logs" / "local-gzctf-real-llm" / f"real-llm-summary-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n[summary written] {out.relative_to(PROJECT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
