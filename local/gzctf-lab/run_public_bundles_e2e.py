#!/usr/bin/env python3
"""End-to-end import + supervisor run for the 8 public-CTF bundles.

Per the operator handoff (2026-05-09 P0):

    public bundle  ->  local GZCTF challenge
       ->  supervisor pulls game/details
       ->  downloads attachment to artifacts/challenges/<local_id>/
       ->  reads pre-staged codex_candidates.json (sidecar simulation)
       ->  validator + FlagGuard + GZCTFAdapter
       ->  POST /api/game/<gid>/challenges/<cid>/submit on local GZCTF
       ->  Accepted / WrongAnswer / FlagSubmitted poll loop

This script is the only thing that touches the local lab; nothing in
this file talks to the public internet, the real DLUT GZCTF, or any
public CTF platform.  It will refuse a non-loopback base URL.

Layout:
    1. Cleanup stale artifacts/challenges/<numeric>/ from prior runs.
    2. Use admin API to create / re-enable 8 challenges, idempotent.
    3. Stage codex outputs at artifacts/challenges/<local_id>/.
    4. Build a custom supervisor cfg (auto_submit_categories widened,
       pwn/reverse force_human_review off, codex_sidecar.enabled on).
    5. Run two ticks (first: download + ingest + submit; second: poll
       any pending), then a third tick with a fresh supervisor instance
       to confirm resume safety.
    6. Print the operator-shaped report.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

# Reuse the existing lab bootstrap helpers (login / req / etc) so we
# don't reinvent the auth + game wiring already covered by tests.
LAB = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB))
import bootstrap_rehearsal as boot  # noqa: E402

# Supervisor module is loaded by spec-style import to match the test
# harness; keeps this script independent of any sys.path tweaks
# the parent already does.
_SUP_PATH = PROJECT / "scripts" / "ai_contest_supervisor.py"
_spec = importlib.util.spec_from_file_location("ai_contest_supervisor", _SUP_PATH)
sup_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sup_mod)  # type: ignore[attr-defined]
AIContestSupervisor = sup_mod.AIContestSupervisor

from ctf_agents.submit.flag_guard import FlagGuard  # noqa: E402
from ctf_agents.submit.gzctf_adapter import GZCTFAdapter  # noqa: E402

BUNDLE_INDEXES = [
    PROJECT / "artifacts" / "public-ctf-platform" / "crypto-web" / "bundle_index.json",
    PROJECT / "artifacts" / "public-ctf-platform" / "rev-pwn" / "bundle_index.json",
]

# Where the lab serves attachment files from (compose.yml mounts this
# directory into the `files:8081` container).  All attachments must be
# copied here under a URL-safe filename.
LAB_FILES_DIR = LAB / "challenges" / "static-attachment"

# Matches every flag used by the 8 public bundles.  The default
# AI-contest config narrows this to flag/dlutctf/dasctf, but public
# CTF flags use DUCTF/HITCON/picoCTF/ROPE/hitcon prefixes and HITCON
# bodies legitimately carry spaces, apostrophes, '?' and '!'.
LAB_FLAG_RE = r"(?i)[a-z][a-z0-9_-]*\{[^{}]{4,400}\}"

# Categories the lab demonstrably auto-submits.  The production
# default keeps pwn/reverse out of auto_submit, but for the public
# bundle test this is the whole point.
LAB_AUTO_CATEGORIES = ["misc", "forensics", "crypto", "web", "reverse", "pwn"]


def assert_loopback() -> None:
    parsed = urlparse(boot.BASE_URL)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit(f"refusing non-local base URL: {boot.BASE_URL!r}")


def safe_attachment_name(bundle_id: str, attachment_relpath: str) -> str:
    """`<bundle_id>__<original_name>`, kept URL-safe so the files
    container can serve it without quoting.  Examples:
        public-cw-1__1337crypt-v2.zip
        public-rp-3__ret2win-player.zip
    """
    name = Path(attachment_relpath).name
    return f"{bundle_id}__{name}"


def load_bundles() -> list[dict]:
    out: list[dict] = []
    for idx in BUNDLE_INDEXES:
        data = json.loads(idx.read_text(encoding="utf-8"))
        for b in data.get("challenges", []):
            cj = json.loads(
                (PROJECT / b["challenge_json"]).read_text(encoding="utf-8")
            )
            out.append({"index": b, "challenge": cj})
    if len(out) != 8:
        raise SystemExit(f"expected 8 bundle entries, found {len(out)}")
    return out


def stage_attachments(bundles: list[dict]) -> None:
    """Copy every bundle attachment into the files container's bind
    mount so GZCTF can serve it via `http://files:8081/<safe_name>`."""
    LAB_FILES_DIR.mkdir(parents=True, exist_ok=True)
    for b in bundles:
        src = PROJECT / b["index"]["attachment_path"]
        if not src.exists():
            raise SystemExit(f"missing bundle attachment: {src}")
        dst = LAB_FILES_DIR / safe_attachment_name(
            b["index"]["id"], b["index"]["attachment_path"]
        )
        shutil.copyfile(src, dst)


def import_bundles_into_lab(admin: requests.Session, game_id: int, bundles: list[dict]) -> dict[str, int]:
    """Create one local GZCTF challenge per bundle.  Returns
    {bundle_id: gzctf_challenge_id}.  Idempotent: if a challenge with
    the same title already exists in the game, it is reused (and its
    flag re-set so this script can repair drifted state)."""
    existing_by_title = {
        c.get("title"): c for c in boot.list_challenges(admin, game_id)
    }
    mapping: dict[str, int] = {}
    for b in bundles:
        bundle_id = b["index"]["id"]
        title = b["challenge"]["title"]
        category = b["challenge"]["category"]
        flag = b["challenge"]["expected_flag"]
        attachment_url = (
            f"http://files:8081/{safe_attachment_name(bundle_id, b['index']['attachment_path'])}"
        )

        existing = existing_by_title.get(title)
        if existing:
            cid = int(existing["id"])
        else:
            # Create disabled first so a transient failure between the
            # POST and the flag-set call doesn't leave GZCTF rejecting
            # subsequent edits ("challenge has no flag and cannot be
            # enabled").  We re-enable below once flag/attachment are set.
            payload = {
                "title": title,
                "category": category,
                "type": "StaticAttachment",
                "isEnabled": False,
                "score": 100,
                "minScore": 100,
                "originalScore": 100,
                "deadlineUtc": None,
            }
            created = boot.as_json(
                boot.req(admin, "POST", f"/api/edit/games/{game_id}/challenges", json=payload)
            )
            if not isinstance(created, dict) or not created.get("id"):
                raise RuntimeError(f"unexpected create response for {bundle_id}: {created}")
            cid = int(created["id"])

        # 1) Replace flags first.  GZCTF's "flags" POST appends, so
        # we delete every prior flag (carry-overs from earlier runs)
        # before inserting the bundle's expected flag.  This is the
        # only place we ever materialise the public flag in transit.
        cur_detail = boot.as_json(boot.req(admin, "GET", f"/api/edit/games/{game_id}/challenges/{cid}"))
        for fl in (cur_detail or {}).get("flags") or []:
            fid = fl.get("id")
            if fid is not None:
                boot.req(admin, "DELETE", f"/api/edit/games/{game_id}/challenges/{cid}/flags/{int(fid)}")
        boot.req(
            admin, "POST",
            f"/api/edit/games/{game_id}/challenges/{cid}/flags",
            json=[{"flag": flag, "attachmentType": "None", "fileHash": None, "remoteUrl": None}],
        )

        # 2) Attachment can be updated freely once a flag exists.
        boot.req(
            admin, "POST",
            f"/api/edit/games/{game_id}/challenges/{cid}/attachment",
            json={"attachmentType": "Remote", "fileHash": None, "remoteUrl": attachment_url},
        )

        # 3) Enable + (re)set category last, now that the challenge has
        # at least one valid flag — this is the step that 400'd before.
        boot.req(
            admin, "PUT",
            f"/api/edit/games/{game_id}/challenges/{cid}",
            json={"isEnabled": True, "category": category, "tag": "Static"},
        )

        mapping[bundle_id] = cid
    return mapping


def cleanup_stale_artifact_dirs(keep_ids: set[str]) -> list[str]:
    """Remove `artifacts/challenges/<numeric>/` from prior runs that
    don't correspond to any current import.  Bundle dirs (public-*)
    are left alone so the source-of-truth attachments / evidence stay
    on disk."""
    root = PROJECT / "artifacts" / "challenges"
    if not root.exists():
        return []
    removed: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if not name.isdigit():
            continue
        if name in keep_ids:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(name)
    return removed


def stage_codex_outputs(mapping: dict[str, int], bundles: list[dict]) -> dict[str, dict]:
    """For each (bundle_id, local_id), copy the bundle's evidence and
    rewrite codex_candidates.json so:
      - challenge_id matches the local GZCTF id (validator pins it)
      - evidence_paths point at artifacts/challenges/<local_id>/...
        which is the only path prefix the validator allows

    Returns metadata so the report can render evidence_path / candidate
    correctly per challenge.
    """
    by_bundle = {b["index"]["id"]: b for b in bundles}
    out: dict[str, dict] = {}
    for bundle_id, local_id in mapping.items():
        b = by_bundle[bundle_id]
        bundle_dir = PROJECT / b["index"]["bundle_dir"]
        cid_str = str(local_id)
        chal_root = PROJECT / "artifacts" / "challenges" / cid_str
        evidence_dir = chal_root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # Mirror evidence files (kept short).  The exact filenames
        # don't matter — only that they exist when validator runs.
        # We keep the bundle's evidence files so the operator can
        # inspect what the simulated Codex pass observed.
        copied_evidence: list[str] = []
        bundle_evidence_dir = bundle_dir / "evidence"
        if bundle_evidence_dir.exists():
            for src in bundle_evidence_dir.iterdir():
                if not src.is_file():
                    continue
                dst = evidence_dir / src.name
                shutil.copyfile(src, dst)
                copied_evidence.append(f"artifacts/challenges/{cid_str}/evidence/{src.name}")

        # Always include a deterministic note file so we have at least
        # one evidence_path even if the bundle's evidence dir is empty.
        synth = evidence_dir / "local_analysis.txt"
        synth.write_text(
            f"# simulated codex notes for {b['challenge']['title']}\n"
            f"bundle_id={bundle_id}\n"
            f"category={b['challenge']['category']}\n"
            f"source={b['challenge'].get('source_url','')}\n",
            encoding="utf-8",
        )
        evidence_paths = list(copied_evidence) + [
            f"artifacts/challenges/{cid_str}/evidence/local_analysis.txt"
        ]

        codex_doc = {
            "challenge_id": cid_str,
            "candidate": b["challenge"]["expected_flag"],
            "confidence": "high",
            "evidence_paths": evidence_paths,
            "submit_recommendation": "never_direct_submit",
            "notes": (
                f"simulated Codex pass for bundle {bundle_id}; expected "
                f"flag was derived from the public source archive, "
                f"never resubmitted upstream"
            ),
        }
        (chal_root / "codex_candidates.json").write_text(
            json.dumps([codex_doc], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        out[bundle_id] = {
            "local_id": local_id,
            "category": b["challenge"]["category"],
            "title": b["challenge"]["title"],
            "attachment_relpath": b["challenge"]["attachment_relpath"],
            "evidence_paths": evidence_paths,
        }
    return out


def build_supervisor_cfg(game_id: int) -> dict:
    return {
        "project": {"name": "public-bundles-e2e", "timezone": "Asia/Shanghai"},
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
            "auto_submit_categories": LAB_AUTO_CATEGORIES,
            "min_conf_auto_submit": 0.60,
            "min_conf_human_review": 0.45,
            "max_wrong_per_challenge": 1,
            "min_seconds_between_submits_global": 0,
            "min_seconds_between_submits_per_challenge": 0,
            "flag_regex": LAB_FLAG_RE,
            "state_path": "state/local-gzctf-bundles/submission_state.json",
            "kill_switch_file": ".auto_submit_off",
            "force_submit_min_reason_length": 10,
            "pwn_reverse_force_human_review": False,
        },
        "agent": {
            # Only matters if codex sidecar fails to produce a candidate;
            # we still list the relevant categories so internal agents
            # could in theory take over.
            "enabled_categories": LAB_AUTO_CATEGORIES,
            "challenge_loop_interval_s": 0,
            "challenge_solve_timeout_s": 60,
            "global_run_timeout_s": 60,
            "heartbeat_interval_s": 0,
        },
        "paths": {
            # Use the default top-level artifacts/ so codex_validator's
            # path policy (`artifacts/challenges/<id>/`) lines up.
            "state_dir": "state/local-gzctf-bundles",
            "artifacts_dir": "artifacts",
            "logs_dir": "logs/local-gzctf-bundles",
            "locks_dir": "state/local-gzctf-bundles/locks",
        },
        # Keep notification wiring on so the report can verify the
        # log carries notify_decision / notify_submit_outcome events.
        # FEISHU_WEBHOOK is intentionally left unset; do_send is False
        # so notifications are logged-but-not-sent.
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


# ---- main --------------------------------------------------------


def collect_per_challenge(sup: AIContestSupervisor, mapping: dict[str, int], staged: dict) -> list[dict]:
    rows = []
    for bundle_id, local_id in mapping.items():
        cstate = sup.state["challenges"].get(str(local_id), {})
        guard_snap = sup.guard.state_store.snapshot()["challenges"].get(str(local_id), {})
        submits = guard_snap.get("submits") or []
        last_submit = submits[-1] if submits else {}
        rows.append({
            "bundle_id": bundle_id,
            "local_id": local_id,
            "title": staged[bundle_id]["title"],
            "category": staged[bundle_id]["category"],
            "supervisor_state": cstate.get("state"),
            "last_submit_kind": last_submit.get("platform_response") or "<none>",
            "last_submit_correct": last_submit.get("correct"),
            "submit_count": len(submits),
            "evidence_paths": staged[bundle_id]["evidence_paths"],
            "attachment_relpath": staged[bundle_id]["attachment_relpath"],
        })
    return rows


def main() -> int:
    # Step 0 — load .env so GZCTF_USERNAME / GZCTF_PASSWORD are visible.
    env_path = LAB / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

    assert_loopback()
    boot.assert_local_base_url()
    boot.wait_ready()

    # Step 1 — admin login + ensure player/team/game.
    admin = boot.login(boot.ADMIN_USERNAME, boot.ADMIN_PASSWORD)
    boot.ensure_player(admin)
    team = boot.ensure_team()
    game = boot.ensure_game(admin)
    game_id = int(game["id"])
    boot.ensure_participation(game_id, int(team["id"]))

    # Step 2 — bundle ingest into the files mount + GZCTF.
    bundles = load_bundles()
    stage_attachments(bundles)
    mapping = import_bundles_into_lab(admin, game_id, bundles)

    # Step 3 — cleanup stale numeric artifact dirs from earlier runs,
    # then stage codex outputs into artifacts/challenges/<local_id>/.
    keep = {str(v) for v in mapping.values()}
    removed_stale = cleanup_stale_artifact_dirs(keep)
    staged = stage_codex_outputs(mapping, bundles)

    # Step 4 — also clean any prior supervisor state file so this run
    # is reproducible from scratch.  (Keep state/locks/ untouched in
    # case anything is mid-flight from another supervisor.)
    state_path = PROJECT / "state" / "local-gzctf-bundles" / "ai_contest_state.json"
    if state_path.exists():
        state_path.unlink()
    submit_state = PROJECT / "state" / "local-gzctf-bundles" / "submission_state.json"
    if submit_state.exists():
        submit_state.unlink()

    cfg = build_supervisor_cfg(game_id)

    # Step 5 — first supervisor instance: download attachments + ingest
    # codex + validate + guard + submit.
    sup1 = build_supervisor(cfg)
    if not sup1.healthcheck():
        print("[fatal] supervisor healthcheck failed", file=sys.stderr)
        return 3

    sup1.run_one_tick()
    # Second tick to terminalize anything still pending after the
    # first round-trip (FlagSubmitted poll loop).
    sup1.run_one_tick()

    rows1 = collect_per_challenge(sup1, mapping, staged)

    # Step 6 — resume safety: spawn a brand-new supervisor that loads
    # the same state files, run a tick, and confirm no extra submits.
    # The fresh adapter needs its own login before run_one_tick or
    # game_details / submit will 401 — that's what healthcheck does.
    sup2 = build_supervisor(cfg)
    if not sup2.healthcheck():
        print("[fatal] resume supervisor healthcheck failed", file=sys.stderr)
        return 4
    sup2.run_one_tick()

    rows2 = collect_per_challenge(sup2, mapping, staged)
    duplicate_blocked = all(
        r1["submit_count"] == r2["submit_count"] for r1, r2 in zip(rows1, rows2)
    )

    # Step 7 — quick sanity: scan logs / state / .secrets for full
    # public-CTF flags.  Anything found is a leak.
    leak_targets = [
        sup1.logger.path,
        state_path,
    ]
    secrets_dir = PROJECT / ".secrets"
    if secrets_dir.exists():
        leak_targets.extend(p for p in secrets_dir.rglob("*") if p.is_file())
    leak_findings: list[dict] = []
    flag_strings = [b["challenge"]["expected_flag"] for b in bundles]
    for target in leak_targets:
        if not target.exists():
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for fl in flag_strings:
            if fl in text:
                leak_findings.append({"file": str(target.relative_to(PROJECT)), "flag_redacted": fl[:6] + "…"})

    # Notification log markers — supervisor logs every Feishu attempt
    # under event_type=notification, with a `data.notification.event`
    # discriminator (accepted / freeze / human_review / kill_switch /
    # force_submit).  Anything sent=True went through the webhook;
    # sent=False means the wiring fired but FEISHU_WEBHOOK was empty.
    notif_seen: dict[str, dict[str, int]] = {}
    if sup1.logger.path.exists():
        for raw in sup1.logger.path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if e.get("event_type") != "notification":
                continue
            note = (e.get("data") or {}).get("notification") or {}
            kind = str(note.get("event") or "?")
            sent = bool(note.get("sent"))
            slot = notif_seen.setdefault(kind, {"sent": 0, "skipped": 0})
            slot["sent" if sent else "skipped"] += 1

    summary = {
        "base_url": boot.BASE_URL,
        "game_id": game_id,
        "team_id": int(team["id"]),
        "mapping": mapping,
        "rows": rows1,
        "rows_after_resume": rows2,
        "duplicate_submit_blocked": duplicate_blocked,
        "removed_stale_artifact_dirs": removed_stale,
        "leak_findings": leak_findings,
        "notification_summary": notif_seen,
        "supervisor_log": str(sup1.logger.path.relative_to(PROJECT)),
    }
    out_path = PROJECT / "logs" / "local-gzctf-bundles" / f"e2e-summary-{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n[summary written] {out_path.relative_to(PROJECT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
