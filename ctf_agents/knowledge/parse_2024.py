"""Linear-text parser for 2024-college PDF.

The 2024 高校网络安全管理运维赛理论赛赛题 PDF uses a linear layout:

    1. 题干？
    A. 选项 A
    B. [正确] 选项 B
    C. 选项 C
    D. 选项 D
    2. 下一题...

Inline ``[正确]`` markers indicate the correct answer(s).  Multi-select
questions have two or more ``[正确]`` markers.  Pure 判断题 do not appear
in this PDF as far as we have seen, but the parser tolerates 0 ``[正确]``
markers and labels such questions ``unknown`` for human audit.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz


QUESTION_HEADER = re.compile(r"(?m)^\s*(\d{1,4})\s*[\.．、]\s*")
OPTION_RE = re.compile(
    r"(?m)^\s*([A-E])\s*[\.．、]\s*(.*?)(?=\n\s*[A-E]\s*[\.．、]|\n\s*\d{1,4}\s*[\.．、]|\Z)",
    re.S,
)
PAGE_HEADER_NUM = re.compile(r"(?m)^\s*\d{1,4}\s*$")
CORRECT_MARK = "[正确]"


def _read_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    parts: list[str] = []
    for page in doc:
        parts.append(page.get_text("text"))
    doc.close()
    return "\n".join(parts)


def _strip_page_artifacts(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if PAGE_HEADER_NUM.match(line):
            continue
        if line.strip() == "":
            out.append("")
            continue
        if "高校网络安全管理运维赛" in line:
            continue
        out.append(line)
    return "\n".join(out)


def _clean_option_text(s: str) -> tuple[str, bool]:
    has_correct = CORRECT_MARK in s
    s2 = s.replace(CORRECT_MARK, "").strip()
    s2 = re.sub(r"\s+", " ", s2)
    return s2, has_correct


def parse_pdf(pdf_path: Path, source: str = "2024-college") -> tuple[list[dict], dict]:
    raw = _read_text(pdf_path)
    text = _strip_page_artifacts(raw)

    matches = list(QUESTION_HEADER.finditer(text))
    questions: list[dict] = []
    for idx, m in enumerate(matches):
        qid_raw = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]

        first_opt = OPTION_RE.search(block)
        if not first_opt:
            stem = block.strip()
            options_block = ""
        else:
            stem = block[: first_opt.start()].strip()
            options_block = block[first_opt.start():]
        stem = re.sub(r"\s+", " ", stem).strip()

        options: list[dict] = []
        answer: list[str] = []
        for opt_m in OPTION_RE.finditer(options_block):
            key = opt_m.group(1)
            text_raw = opt_m.group(2).strip()
            text_clean, is_correct = _clean_option_text(text_raw)
            options.append({"key": key, "text": text_clean})
            if is_correct:
                answer.append(key)

        if len(answer) > 1:
            qtype = "multi"
        elif len(answer) == 1:
            qtype = "single"
        else:
            qtype = "unknown"

        flags: list[str] = []
        if not options:
            flags.append("no_options")
        if not answer:
            flags.append("no_correct_marker")
        if len(options) < 2 and qtype != "judge":
            flags.append("too_few_options")
        if not stem:
            flags.append("empty_stem")

        try:
            qid_int = int(qid_raw)
        except ValueError:
            continue

        questions.append(
            {
                "qid": "",
                "source": source,
                "source_qid": qid_raw,
                "source_qid_int": qid_int,
                "page": -1,
                "type": qtype,
                "type_raw": qtype,
                "stem_raw": stem,
                "options_raw": options,
                "answer": answer,
                "answer_text_raw": "".join(answer) if answer else "",
                "answer_col": -1,
                "flags": flags,
            }
        )

    for seq, q in enumerate(questions, start=1):
        q["qid"] = f"{source}-{seq:04d}"
        q["parsed_seq"] = seq

    qids = [q["source_qid_int"] for q in questions]
    duplicates = sorted({n for n in qids if qids.count(n) > 1}) if qids else []
    if qids:
        max_qid = max(qids)
        present = set(qids)
        gaps = [n for n in range(1, max_qid + 1) if n not in present]
    else:
        max_qid = 0
        gaps = []

    summary = {
        "source": source,
        "questions_parsed": len(questions),
        "min_qid": min(qids) if qids else 0,
        "max_qid": max_qid,
        "duplicate_qids": duplicates,
        "missing_qids": gaps,
        "judge": sum(1 for q in questions if q["type"] == "judge"),
        "single": sum(1 for q in questions if q["type"] == "single"),
        "multi": sum(1 for q in questions if q["type"] == "multi"),
        "unknown": sum(1 for q in questions if q["type"] == "unknown"),
        "flagged": sum(1 for q in questions if q["flags"]),
    }
    return questions, summary
