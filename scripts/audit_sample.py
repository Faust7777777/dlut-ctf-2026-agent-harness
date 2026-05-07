#!/usr/bin/env python3
"""Sample N questions across all 5 PDFs for human audit.

Sampling strategy:
  - stratify by PDF source (~6 questions per PDF)
  - within each PDF, stratify by type (judge/single/multi) proportionally
  - reproducible via seed argument

Output:
  reports/audit_sample.csv   — for easy spreadsheet review
  reports/audit_sample.md    — for terminal/markdown review
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

BANK = PROJECT / "data" / "processed" / "question_bank_merged.json"
OUT_CSV = PROJECT / "reports" / "audit_sample.csv"
OUT_MD = PROJECT / "reports" / "audit_sample.md"

SOURCES = ["2020-content", "2020-tech", "2020-compliance", "2020-practice", "2024-college"]


def stratified_sample(questions: list[dict], n: int, rng: random.Random) -> list[dict]:
    by_source: dict[str, list[dict]] = {s: [] for s in SOURCES}
    for q in questions:
        if q["source"] in by_source:
            by_source[q["source"]].append(q)

    per_source = n // len(SOURCES)
    extra = n - per_source * len(SOURCES)

    picked: list[dict] = []
    for i, src in enumerate(SOURCES):
        target = per_source + (1 if i < extra else 0)
        pool = by_source[src]
        if not pool:
            continue
        by_type: dict[str, list[dict]] = {}
        for q in pool:
            by_type.setdefault(q["type"], []).append(q)
        type_props = {t: len(qs) / len(pool) for t, qs in by_type.items()}
        type_target: dict[str, int] = {}
        running = 0
        sorted_types = sorted(type_props.keys(), key=lambda t: -type_props[t])
        for t in sorted_types[:-1]:
            type_target[t] = max(1, round(type_props[t] * target))
            running += type_target[t]
        if sorted_types:
            type_target[sorted_types[-1]] = max(0, target - running)
        for t, k in type_target.items():
            sub = rng.sample(by_type[t], min(k, len(by_type[t])))
            picked.extend(sub)
    rng.shuffle(picked)
    return picked


def write_csv(samples: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "qid",
                "source",
                "source_qid",
                "page",
                "type",
                "stem",
                "options",
                "answer",
                "auditor_stem_ok",
                "auditor_options_ok",
                "auditor_answer_ok",
                "auditor_notes",
            ]
        )
        for q in samples:
            opts = " | ".join(f"{o['key']}: {o['text']}" for o in q.get("options_raw", []))
            ans = "".join(q.get("answer", []))
            w.writerow(
                [
                    q["qid"],
                    q["source"],
                    q.get("source_qid", ""),
                    q.get("page", ""),
                    q["type"],
                    q.get("stem_raw", ""),
                    opts,
                    ans,
                    "",
                    "",
                    "",
                    "",
                ]
            )


def write_md(samples: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# 30题人工抽样审计")
    lines.append("")
    lines.append(f"共 {len(samples)} 题，覆盖 5 份 PDF。")
    lines.append("")
    lines.append("审计目标（不验证答案对错，只验证解析正确性）：")
    lines.append("- [ ] 题干完整、未截断")
    lines.append("- [ ] 选项数量与文本完整（注意是否有「非否」这类断尾）")
    lines.append("- [ ] 答案标签正确对应到这道题")
    lines.append("- [ ] 题型识别正确")
    lines.append("")
    by_source: dict[str, list[dict]] = {}
    for q in samples:
        by_source.setdefault(q["source"], []).append(q)
    for src in SOURCES:
        if src not in by_source:
            continue
        lines.append(f"## {src} ({len(by_source[src])} 题)")
        lines.append("")
        for q in by_source[src]:
            lines.append(f"### {q['qid']} (源题号 {q.get('source_qid', '?')}, 类型 {q['type']}, 第 {q.get('page', '?')} 页)")
            lines.append("")
            lines.append(f"**题干**：{q.get('stem_raw', '')}")
            lines.append("")
            for opt in q.get("options_raw", []):
                lines.append(f"- {opt['key']}. {opt['text']}")
            if q.get("options_raw"):
                lines.append("")
            ans = "".join(q.get("answer", []))
            ans_label = ""
            if q["type"] == "judge":
                ans_label = " (即 '正确')" if ans == "T" else (" (即 '错误')" if ans == "F" else "")
            lines.append(f"**答案**：{ans}{ans_label}")
            lines.append("")
            lines.append("审计：")
            lines.append("- [ ] 题干 OK")
            lines.append("- [ ] 选项 OK")
            lines.append("- [ ] 答案 OK")
            lines.append("- [ ] 题型 OK")
            lines.append("- 备注：")
            lines.append("")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20260507)
    args = ap.parse_args()

    bank = json.loads(BANK.read_text(encoding="utf-8"))
    qs = bank["questions"]
    rng = random.Random(args.seed)
    samples = stratified_sample(qs, args.num, rng)

    write_csv(samples, OUT_CSV)
    write_md(samples, OUT_MD)

    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for q in samples:
        by_source[q["source"]] = by_source.get(q["source"], 0) + 1
        by_type[q["type"]] = by_type.get(q["type"], 0) + 1

    print(f"sampled {len(samples)} questions (seed={args.seed})")
    print(f"  by source: {by_source}")
    print(f"  by type:   {by_type}")
    print(f"  csv: {OUT_CSV}")
    print(f"  md:  {OUT_MD}")


if __name__ == "__main__":
    main()
