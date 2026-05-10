#!/usr/bin/env python3
"""Build a per-paper wjx manifest with a fresh bank sha256.

Usage::

    python scripts/build_paper_manifest.py \\
        --paper-id mBfE06C \\
        --url 'https://ks.wjx.com/vm/mBfE06C.aspx#' \\
        --static-answers examples/dlut_bank_wjx_import_300_answers.json \\
        --bank data/processed/question_bank_merged.json \\
        --output examples/wjx_mBfE06C_manifest.json

Adds an empty ``verified_overrides`` array.  Operators amend the array
by hand after dry-runs surface specific question conflicts.

The script is intentionally read-only on the bank: its sole role is to
record the bank's sha256 at import time so wjx_exam_assist can later
refuse static fallback if the bank changes underfoot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-id", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--bank", default=str(PROJECT / "data" / "processed" / "question_bank_merged.json"))
    ap.add_argument("--static-answers", default="")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    bank_path = Path(args.bank)
    if not bank_path.exists():
        print(f"bank not found: {bank_path}", file=sys.stderr)
        return 2
    bank_sha = sha256_of(bank_path)

    bank_rel = (
        bank_path.relative_to(PROJECT) if bank_path.is_relative_to(PROJECT) else bank_path
    )

    static_block: dict | None = None
    if args.static_answers:
        static_path = Path(args.static_answers)
        if not static_path.is_absolute():
            static_path = (PROJECT / static_path).resolve()
        if not static_path.exists():
            print(f"static answers not found: {static_path}", file=sys.stderr)
            return 3
        static_rel = (
            static_path.relative_to(PROJECT)
            if static_path.is_relative_to(PROJECT)
            else static_path
        )
        static_block = {
            "path": str(static_rel),
            "sha256": sha256_of(static_path),
        }

    manifest = {
        "paper_id": args.paper_id,
        "url": args.url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bank": {
            "path": str(bank_rel),
            "sha256": bank_sha,
        },
        # Same-bank trust boundary: wjx_exam_assist verifies the on-disk
        # bank file matches `bank.sha256` AND the answers file (whether
        # passed via --answers or adopted from this manifest) matches
        # `static_answers.sha256`.  Mismatch → static fallback refused.
        "static_answers": static_block,
        "verified_overrides": [],
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest written: {out}")
    print(f"  paper_id: {args.paper_id}")
    print(f"  bank_sha: {bank_sha}")
    if static_block:
        print(f"  static_answers: {static_block['path']}")
        print(f"  static_answers_sha: {static_block['sha256']}")
    else:
        print(f"  static_answers: <none — fallback will be unavailable>")
    print(f"  verified_overrides: 0 (edit by hand after dry-run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
