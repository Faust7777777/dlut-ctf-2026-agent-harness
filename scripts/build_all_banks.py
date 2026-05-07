#!/usr/bin/env python3
"""Build per-PDF question banks and a merged bank.

Inputs:
  data/raw/<short>.pdf  (5 PDFs after ingest_pdfs.py)

Outputs:
  data/processed/<short>/question_bank.json
  data/processed/<short>/audit.csv
  data/processed/question_bank_merged.json
  data/processed/build_summary.json
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.knowledge import parse_2020, parse_2024  # noqa: E402
from ctf_agents.knowledge.bank_fixes import apply_known_fixes_to_questions  # noqa: E402

PDF_PARSERS = [
    ("2020-content",    parse_2020.parse_pdf),
    ("2020-tech",       parse_2020.parse_pdf),
    ("2020-compliance", parse_2020.parse_pdf),
    ("2020-practice",   parse_2020.parse_pdf),
    ("2024-college",    parse_2024.parse_pdf),
]


def write_audit_csv(questions: list[dict], out: Path) -> None:
    rows_to_audit = [q for q in questions if q.get("flags")]
    rows_to_audit.sort(key=lambda q: (q["source"], q["source_qid_int"]))
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "qid",
            "source",
            "source_qid",
            "page",
            "type",
            "flags",
            "stem_preview",
            "options_count",
            "answer",
        ])
        for q in rows_to_audit:
            writer.writerow([
                q["qid"],
                q["source"],
                q["source_qid"],
                q.get("page", ""),
                q["type"],
                "|".join(q.get("flags", [])),
                (q.get("stem_raw") or "")[:60],
                len(q.get("options_raw", [])),
                "".join(q.get("answer", [])),
            ])


def main() -> None:
    raw_dir = PROJECT / "data" / "raw"
    proc_dir = PROJECT / "data" / "processed"
    proc_dir.mkdir(parents=True, exist_ok=True)

    all_questions: list[dict] = []
    summaries: list[dict] = []

    for short, parser in PDF_PARSERS:
        pdf_path = raw_dir / f"{short}.pdf"
        if not pdf_path.exists():
            print(f"[MISS] {short}: {pdf_path}")
            continue
        print(f"[PARSE] {short} ...")
        questions, summary = parser(pdf_path, short)
        questions = apply_known_fixes_to_questions(questions)
        summary["manual_fixed"] = sum(
            1
            for q in questions
            if any(str(flag).startswith("manual_fix:") for flag in q.get("flags", []))
        )
        summary["manual_review_required"] = sum(
            1
            for q in questions
            if any(str(flag).startswith("manual_review_required:") for flag in q.get("flags", []))
        )

        per_dir = proc_dir / short
        per_dir.mkdir(parents=True, exist_ok=True)
        bank = {
            "source": {
                "short": short,
                "pdf_path": str(pdf_path),
                "parser": parser.__module__,
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "questions": questions,
        }
        (per_dir / "question_bank.json").write_text(
            json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_audit_csv(questions, per_dir / "audit.csv")

        print(
            f"  parsed={summary['questions_parsed']:5d}  "
            f"判={summary.get('judge', 0):4d}  "
            f"单={summary.get('single', 0):4d}  "
            f"多={summary.get('multi', 0):4d}  "
            f"unknown={summary.get('unknown', 0):4d}  "
            f"flagged={summary['flagged']:4d}  "
            f"qid_range={summary['min_qid']}-{summary['max_qid']}  "
            f"missing={len(summary['missing_qids']):4d}  "
            f"dup={len(summary['duplicate_qids']):4d}"
        )

        all_questions.extend(questions)
        summaries.append(summary)

    merged_path = proc_dir / "question_bank_merged.json"
    merged = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summaries": summaries,
        "questions": all_questions,
    }
    merged_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary_path = proc_dir / "build_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_questions": len(all_questions),
                "per_pdf": summaries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[MERGED] {merged_path}  total={len(all_questions)}")
    print(f"[SUMMARY] {summary_path}")


if __name__ == "__main__":
    main()
