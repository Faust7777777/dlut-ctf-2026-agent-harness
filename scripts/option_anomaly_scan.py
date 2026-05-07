#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_BANK = PROJECT / "data" / "processed" / "question_bank_merged.json"
DEFAULT_CSV = PROJECT / "reports" / "option_anomaly.csv"
DEFAULT_MD = PROJECT / "reports" / "option_anomaly.md"

URL_RE = re.compile(r"^https?://", re.I)
QUESTION_LIKE_RE = re.compile(r"[？?]|^下列|^以下|哪个说法")
ODD_FRAGMENT_RE = re.compile(r"(非否$|本地储备份|存备份本地|计算机络)")
TRUNCATED_SUFFIX_RE = re.compile(r"(大规模用$|小规模用$|个人信$|服务提$|网络运$)")

SOURCE_THREE_OPTION_SINGLE_QIDS = {
    "2020-practice-0054",
    "2020-practice-0055",
    "2020-practice-0056",
}
SOURCE_SIX_OPTION_QIDS = {
    "2020-compliance-0752",
}


def _option_keys(q: dict) -> list[str]:
    return [str(o.get("key", "")).strip().upper() for o in q.get("options_raw", [])]


def _answer_max_index(answer: list[str]) -> int:
    idxs = [ord(a) - ord("A") for a in answer if isinstance(a, str) and len(a) == 1 and "A" <= a <= "Z"]
    return max(idxs) if idxs else -1


def analyze_question(q: dict) -> list[str]:
    issues: list[str] = []
    qtype = q.get("type")
    options = q.get("options_raw", [])
    answer = q.get("answer", [])

    if qtype in {"single", "multi"}:
        if not options:
            issues.append("no_options")
        if (
            len(options) < 4
            and not (qtype == "single" and q.get("qid") in SOURCE_THREE_OPTION_SINGLE_QIDS)
        ):
            issues.append("few_options")
        if len(options) > 5 and q.get("qid") not in SOURCE_SIX_OPTION_QIDS:
            issues.append("too_many_options")

        keys = _option_keys(q)
        expected = [chr(ord("A") + i) for i in range(len(keys))]
        if keys != expected:
            issues.append("non_contiguous_option_keys")

        if _answer_max_index(answer) >= len(options):
            issues.append("answer_letter_outside_options")

        for opt in options:
            text = str(opt.get("text", "")).strip()
            compact = re.sub(r"\s+", "", text)
            if not compact:
                issues.append("empty_option")
                continue
            if ODD_FRAGMENT_RE.search(compact):
                issues.append("suspicious_fragment")
            if TRUNCATED_SUFFIX_RE.search(compact):
                issues.append("possible_truncated_option")
            if QUESTION_LIKE_RE.search(compact) and len(compact) > 18:
                issues.append("option_contains_question_like_text")
            if "  " in text or "\t" in text:
                issues.append("option_whitespace_artifact")
            if URL_RE.search(compact) and len(compact) < 14:
                issues.append("truncated_url_option")

    if qtype == "judge" and options:
        issues.append("judge_has_options")

    return sorted(set(issues))


def _row_for(q: dict, issues: list[str]) -> dict[str, str]:
    opts = " | ".join(f"{o.get('key')}: {o.get('text')}" for o in q.get("options_raw", []))
    return {
        "qid": q.get("qid", ""),
        "source": q.get("source", ""),
        "source_qid": q.get("source_qid", ""),
        "page": str(q.get("page", "")),
        "type": q.get("type", ""),
        "issues": "|".join(issues),
        "answer": "".join(q.get("answer", [])),
        "options_count": str(len(q.get("options_raw", []))),
        "stem": q.get("stem_raw", ""),
        "options": opts,
    }


def write_reports(rows: list[dict[str, str]], csv_path: Path, md_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "qid",
        "source",
        "source_qid",
        "page",
        "type",
        "issues",
        "answer",
        "options_count",
        "stem",
        "options",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    by_issue = Counter()
    by_source = defaultdict(int)
    for r in rows:
        by_source[r["source"]] += 1
        for issue in r["issues"].split("|"):
            by_issue[issue] += 1

    lines = ["# Option Anomaly Report", ""]
    lines.append(f"Total suspicious questions: {len(rows)}")
    lines.append("")
    lines.append("## By Issue")
    lines.append("")
    for issue, count in by_issue.most_common():
        lines.append(f"- `{issue}`: {count}")
    lines.append("")
    lines.append("## By Source")
    lines.append("")
    for source, count in sorted(by_source.items()):
        lines.append(f"- `{source}`: {count}")
    lines.append("")
    lines.append("## Top Rows")
    lines.append("")
    for r in rows[:80]:
        lines.append(f"### {r['qid']} ({r['source']} #{r['source_qid']}, page {r['page']})")
        lines.append("")
        lines.append(f"- Issues: `{r['issues']}`")
        lines.append(f"- Type/answer/options: {r['type']} / {r['answer']} / {r['options_count']}")
        lines.append(f"- Stem: {r['stem']}")
        lines.append(f"- Options: {r['options']}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    bank = json.loads(DEFAULT_BANK.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for q in bank.get("questions", []):
        issues = analyze_question(q)
        if issues:
            rows.append(_row_for(q, issues))
    rows.sort(key=lambda r: (r["source"], int(r["page"]) if r["page"].lstrip("-").isdigit() else 99999, r["qid"]))
    write_reports(rows, DEFAULT_CSV, DEFAULT_MD)
    print(f"rows={len(rows)}")
    print(DEFAULT_CSV)
    print(DEFAULT_MD)


if __name__ == "__main__":
    main()
