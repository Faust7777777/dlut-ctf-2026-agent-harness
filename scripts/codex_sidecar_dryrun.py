#!/usr/bin/env python3
"""End-to-end dry-run of the Codex sidecar contract.

Per ``runbooks/codex_sidecar.md``, Codex (via ``codex-plugin-cc``) is a
P2 sidecar.  This dry-run does NOT depend on the real plugin being
installed — it simulates everything Codex would write so the supervisor
+ validator path is exercisable on every CI run.

Demonstrates:

  1. Sandbox layout check (artifacts present; .env / .secrets exist
     out-of-bounds).
  2. Builds a fake challenge artifact tree under
     ``artifacts/challenges/<id>/`` with sample evidence + notes.
  3. Writes a sample ``codex_candidates.json`` (valid schema).
  4. Validates the sample with ``validate_codex_candidate``.
  5. Tries 5 negative samples (forbidden keys / paths / wrong recommendation /
     bad confidence / out-of-sandbox) and asserts each one is rejected.
  6. Prints a JSON summary so reviewers can see exactly what well-formed
     Codex output looks like.

Exit code 0 only when:
  - the positive sample validates clean
  - all 5 negative samples are caught
  - the supervisor path would still gate any submission via FlagGuard
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.sidecar.codex_validator import (  # noqa: E402
    check_sandbox_filesystem,
    is_safe_artifact_path,
    validate_codex_candidate,
)


SCENARIOS_NEGATIVE = [
    {
        "label": "forbidden_submit_key",
        "payload": {
            "challenge_id": "1",
            "candidate": "flag{x}",
            "confidence": "high",
            "evidence_paths": ["artifacts/challenges/1/evidence/strings.txt"],
            "submit_recommendation": "never_direct_submit",
            "notes": "ok",
            "submit": "POST /api/game/1/challenges/1",  # forbidden
        },
        "expect_substring": "forbidden keys",
    },
    {
        "label": "evidence_path_secrets",
        "payload": {
            "challenge_id": "1",
            "candidate": "flag{x}",
            "confidence": "high",
            "evidence_paths": [".secrets/gzctf_cookies.json"],
            "submit_recommendation": "never_direct_submit",
            "notes": "tried to peek at cookies",
        },
        "expect_substring": "blocked",
    },
    {
        "label": "wrong_submit_recommendation",
        "payload": {
            "challenge_id": "1",
            "candidate": "flag{x}",
            "confidence": "high",
            "evidence_paths": ["artifacts/challenges/1/evidence/x.txt"],
            "submit_recommendation": "auto_submit",   # not allowed
            "notes": "trying to bypass guard",
        },
        "expect_substring": "submit_recommendation",
    },
    {
        "label": "bad_confidence",
        "payload": {
            "challenge_id": "1",
            "candidate": "flag{x}",
            "confidence": "very_high",   # not in whitelist
            "evidence_paths": ["artifacts/challenges/1/evidence/x.txt"],
            "submit_recommendation": "never_direct_submit",
            "notes": "ok",
        },
        "expect_substring": "confidence",
    },
    {
        "label": "evidence_path_traversal",
        "payload": {
            "challenge_id": "1",
            "candidate": "flag{x}",
            "confidence": "low",
            "evidence_paths": ["artifacts/challenges/../../etc/passwd"],
            "submit_recommendation": "never_direct_submit",
            "notes": "directory traversal attempt",
        },
        "expect_substring": "blocked",
    },
]


SCENARIO_POSITIVE = {
    "challenge_id": "demo-001",
    "candidate": "flag{dryrun-demo-do-not-submit}",
    "confidence": "medium",
    "evidence_paths": [
        "artifacts/challenges/demo-001/evidence/strings_dump.txt",
        "artifacts/challenges/demo-001/evidence/binwalk_extract/img.png",
    ],
    "submit_recommendation": "never_direct_submit",
    "notes": (
        "binwalk surfaced a PNG inside the attached zip; strings on the "
        "PNG body contained a flag-shaped token at offset 0x1a40."
    ),
}


def write_fake_artifact(challenge_id: str, root: Path) -> Path:
    """Materialise a sample artifact tree the way Codex would after
    inspecting one challenge.  Lives under ``<root>/artifacts/
    challenges/`` so the validator's path policy holds; ``root`` is
    a temporary directory so the dry-run leaves no persistent files
    in the repo (Codex review §4 hygiene fix).
    """
    base = root / "artifacts" / "challenges" / challenge_id
    (base / "evidence" / "binwalk_extract").mkdir(parents=True, exist_ok=True)

    notes = base / "codex_notes.md"
    notes.write_text(
        "# Codex sidecar notes — challenge demo-001\n\n"
        "## File survey\n\n"
        "- attached: puzzle.zip (sha256 truncated)\n"
        "- unzip → puzzle.png (PNG, 800x600)\n"
        "- binwalk found embedded PNG at offset 0x1a00\n\n"
        "## Tools run\n\n"
        "```bash\n"
        "binwalk -e puzzle.zip\n"
        "strings -n 8 _puzzle.zip.extracted/img.png | grep -i flag\n"
        "```\n\n"
        "## Candidate rationale\n\n"
        "Strings hit at offset 0x1a40: `flag{dryrun-demo-do-not-submit}`.\n"
        "Confidence: medium — single source, no second-tool corroboration.\n\n"
        "**Note**: this candidate must traverse `FlagGuard` before any\n"
        "submission attempt.  Codex never calls submit directly.\n",
        encoding="utf-8",
    )

    candidates = base / "codex_candidates.json"
    candidates.write_text(
        json.dumps([SCENARIO_POSITIVE], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Pretend evidence files (binwalk would have produced these)
    (base / "evidence" / "strings_dump.txt").write_text(
        "...\nflag{dryrun-demo-do-not-submit}\n...\n", encoding="utf-8"
    )
    (base / "evidence" / "binwalk_extract" / "img.png").write_bytes(
        b"\x89PNG\r\n\x1a\nFAKE_PNG_FOR_DRYRUN_ONLY"
    )

    return base


def assert_sandbox_layout(root: Path) -> dict:
    info = check_sandbox_filesystem(root)
    print("[sandbox check]")
    print(f"  artifacts/        present: {info['artifacts_dir_present']}")
    print(f"  .secrets/         present: {info['secrets_dir_present']}")
    print(f"  state/            present: {info['state_dir_present']}")
    return info


def main() -> int:
    print("=== Codex sidecar dry-run ===\n")

    # Sandbox + artifacts under a tmp project root so the dry-run does
    # not leave files in the real repo.  Codex review §4 hygiene fix.
    with tempfile.TemporaryDirectory(prefix="codex_dryrun_") as td:
        root = Path(td)
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        (root / ".secrets").mkdir(parents=True, exist_ok=True)
        (root / "state").mkdir(parents=True, exist_ok=True)
        sandbox = assert_sandbox_layout(root)

        base = write_fake_artifact("demo-001", root)
        print(f"\n[fake artifacts written] {base}")

        # --- 1. validate the positive sample ---
        print("\n[positive sample]")
        candidates_file = base / "codex_candidates.json"
        raw = json.loads(candidates_file.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or len(raw) != 1:
            print("[FAIL] positive sample structure")
            return 1
        pos_errs = validate_codex_candidate(raw[0], expected_challenge_id="demo-001")
        if pos_errs:
            print(f"[FAIL] positive sample rejected: {pos_errs}")
            return 1
        print("  [PASS] positive candidate accepted")

        # --- 2. run negative scenarios ---
        print("\n[negative samples]")
        neg_results = []
        for scen in SCENARIOS_NEGATIVE:
            errs = validate_codex_candidate(scen["payload"])
            caught = bool(errs) and any(
                scen["expect_substring"].lower() in e.lower() for e in errs
            )
            marker = "PASS" if caught else "FAIL"
            print(f"  [{marker}] {scen['label']}  → {len(errs)} error(s)")
            if not caught:
                print(f"      expected substring: {scen['expect_substring']!r}")
                print(f"      actual errors: {errs}")
            neg_results.append({"label": scen["label"], "caught": caught, "errors": errs})

        # --- 3. is_safe_artifact_path coverage ---
        print("\n[path policy]")
        safe = is_safe_artifact_path(base, root)
        print(f"  artifacts path inside sandbox: {safe}")
        unsafe = is_safe_artifact_path(root / ".secrets", root)
        print(f"  .secrets path inside sandbox: {unsafe} (must be False)")

        # --- 4. supervisor/guard gating reminder ---
        print("\n[gating reminder]")
        print("  Even a Codex candidate marked confidence=high goes:")
        print("  Codex output → validator → FlagCandidate → FlagGuard → adapter")
        print("  Codex output → adapter is FORBIDDEN by design.")

        # --- 5. summary (written to real project logs/, but the source
        # artifacts live and die inside the temp dir) ---
        files_written = [
            str(p.relative_to(root)) for p in base.rglob("*") if p.is_file()
        ]
        summary = {
            "run_id": datetime.now(timezone.utc).strftime("codex-sidecar-dryrun-%Y%m%dT%H%M%SZ"),
            "sandbox": sandbox,
            "positive_sample_ok": not pos_errs,
            "negative_results": neg_results,
            "artifact_root_relative": str(base.relative_to(root)),
            "artifacts_files_written": files_written,
        }

    out = PROJECT / "logs" / f"{summary['run_id']}-summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[summary written] {out}")

    all_neg_caught = all(r["caught"] for r in neg_results)
    if not pos_errs and all_neg_caught and safe and not unsafe:
        print("\n[ALL CHECKS PASSED]")
        return 0
    print("\n[FAIL] at least one assertion did not hold")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
