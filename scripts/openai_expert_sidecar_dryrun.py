#!/usr/bin/env python3
"""Mock dry-run for the OpenAI expert sidecar contract.

No OpenAI API call is made.  The script proves the advisory sidecar can
bundle one challenge, write fixed output files, validate candidates,
and reject budget/path/forbidden-read failures without touching the
real repository's artifacts, state, logs, or secrets.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.sidecar.openai_expert import (  # noqa: E402
    ExpertSidecarConfig,
    api_key_status,
    build_challenge_manifest,
    run_expert,
)


def _seed_demo(root: Path, challenge_id: str) -> Path:
    base = root / "artifacts" / "challenges" / challenge_id
    evidence = base / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (base / "README.txt").write_text("Expert sidecar demo challenge\n", encoding="utf-8")
    (evidence / "strings.txt").write_text(
        "offset 0x20: flag{expert-dryrun-do-not-submit}\n",
        encoding="utf-8",
    )
    return base


def _marker_snapshot(root: Path) -> dict[str, bool]:
    return {
        ".env": (root / ".env").exists(),
        ".secrets": (root / ".secrets").exists(),
        "state": (root / "state").exists(),
        "logs": (root / "logs").exists(),
    }


def main() -> int:
    print("=== OpenAI expert sidecar dry-run ===")
    print(f"  api_key: {api_key_status()}")
    print("  live_api: disabled (mock response only)")

    with tempfile.TemporaryDirectory(prefix="openai_expert_dryrun_") as td:
        root = Path(td)
        (root / "artifacts" / "challenges").mkdir(parents=True)
        (root / ".secrets").mkdir()
        (root / "state").mkdir()
        (root / "logs").mkdir()
        before = _marker_snapshot(root)

        challenge_id = "expert-demo"
        challenge_dir = _seed_demo(root, challenge_id)
        cfg = ExpertSidecarConfig(
            enabled=True,
            max_input_files=5,
            max_attachment_mb=1,
            allowed_categories=("misc", "crypto"),
        )
        mock_response = {
            "notes": (
                "# Expert notes\n\n"
                "Read only the provided artifact bundle. strings.txt contains "
                "a single flag-shaped token. This remains advisory."
            ),
            "candidates": [
                {
                    "challenge_id": challenge_id,
                    "category": "misc",
                    "candidate": "flag{expert-dryrun-do-not-submit}",
                    "confidence": "high",
                    "evidence_paths": [
                        f"artifacts/challenges/{challenge_id}/expert_notes.md",
                        f"artifacts/challenges/{challenge_id}/evidence/strings.txt",
                    ],
                    "submit_recommendation": "never_direct_submit",
                    "notes": "strings evidence supports this token",
                }
            ],
        }

        result = run_expert(
            challenge_dir,
            challenge_id=challenge_id,
            category="misc",
            config=cfg,
            project_root=root,
            mock_response=mock_response,
        )
        print(f"  positive: status={result.status} valid_candidates={result.valid_candidates}")

        checks: list[tuple[str, bool, str]] = []
        checks.append(("positive_mock_response", result.status == "ok", result.status))
        checks.append(("fixed_notes_path", result.notes_path.endswith("/expert_notes.md"), result.notes_path))
        checks.append(("fixed_candidates_path", result.candidates_path.endswith("/expert_candidates.json"), result.candidates_path))

        outside = root / "tmp" / challenge_id
        outside.mkdir(parents=True)
        try:
            build_challenge_manifest(outside, challenge_id=challenge_id, config=cfg, project_root=root)
            path_rejected = False
            path_msg = "accepted outside path"
        except ValueError as exc:
            path_rejected = True
            path_msg = str(exc)
        checks.append(("sandbox_path_reject", path_rejected, path_msg))

        big = challenge_dir / "big.bin"
        big.write_bytes(b"x" * 2048)
        tiny_budget = ExpertSidecarConfig(enabled=True, max_attachment_mb=0.001)
        try:
            build_challenge_manifest(challenge_dir, challenge_id=challenge_id, config=tiny_budget, project_root=root)
            budget_rejected = False
            budget_msg = "accepted oversized file"
        except ValueError as exc:
            budget_rejected = True
            budget_msg = str(exc)
        checks.append(("budget_reject", budget_rejected, budget_msg))
        big.unlink()

        forbidden = run_expert(
            challenge_dir,
            challenge_id=challenge_id,
            category="misc",
            config=cfg,
            project_root=root,
            mock_response={
                "notes": "tries forbidden evidence",
                "candidates": [
                    {
                        "challenge_id": challenge_id,
                        "candidate": "flag{bad}",
                        "confidence": "high",
                        "evidence_paths": [".secrets/openai_key.txt"],
                        "submit_recommendation": "never_direct_submit",
                        "notes": "bad path",
                    }
                ],
            },
        )
        checks.append(("forbidden_read_reject", forbidden.status == "invalid_candidates", forbidden.status))

        budgeted = run_expert(
            challenge_dir,
            challenge_id=challenge_id,
            category="misc",
            config=ExpertSidecarConfig(
                enabled=True,
                default_model="dryrun-model",
                api_base_url="https://unit.test/v1",
                budget_usd_soft_limit=1.0,
            ),
            project_root=root,
            budget_spent_usd=1.0,
        )
        checks.append(("cost_budget_reject", budgeted.status == "cost_budget_exhausted", budgeted.status))

        after = _marker_snapshot(root)
        checks.append(("forbidden_dirs_unchanged", before == after, json.dumps({"before": before, "after": after})))

        for name, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

        summary = {
            "run_id": datetime.now(timezone.utc).strftime("openai-expert-dryrun-%Y%m%dT%H%M%SZ"),
            "api_key_status": api_key_status(),
            "positive_result": result.to_dict(),
            "forbidden_result": forbidden.to_dict(),
            "budget_result": budgeted.to_dict(),
            "checks": [
                {"name": name, "ok": ok, "detail": detail}
                for name, ok, detail in checks
            ],
        }
        out = root / "dryrun_summary.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  summary: {out}")

    if all(ok for _, ok, _ in checks):
        print("[ALL CHECKS PASSED]")
        return 0
    print("[FAIL] at least one check failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
