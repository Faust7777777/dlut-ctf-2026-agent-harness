"""Table-based parser for 2020 国赛 PDFs.

Each PDF page contains a table with columns:
  题型 | 序号 | 题目描述 | A | B | C | D | E | 正确答案 | 难度系数 | 答案说明

Some PDFs omit 难度系数 / 答案说明.
The 备选答案 group spans columns 4-8 (A-E); some 判断题 leave them empty.

Known pdfplumber failure modes:
  - Multi-line option cells: the wrapped continuation may leak into the
    next row's option columns.
  - Header rows (题型/序号/题目描述/...) appear once per page and must be skipped.
  - Some pages (rare) yield 0 tables.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pdfplumber


JUDGE_TRUE = {"对", "正确", "T", "True", "true", "TRUE", "√", "是"}
JUDGE_FALSE = {"错", "错误", "F", "False", "false", "FALSE", "×", "否"}
QTYPE_HEADERS = {"题型"}
QTYPES_RAW = {"判断题", "单选题", "多选题"}
QTYPE_NORM = {"判断题": "judge", "单选题": "single", "多选题": "multi"}

ANSWER_LETTERS = re.compile(r"^[A-G]+$")


def _clean_cell(s: Optional[str]) -> str:
    if s is None:
        return ""
    return s.replace("\n", "").replace("\r", "").strip()


def _strip_inner_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _is_plausible_answer(raw: str, qtype: str) -> bool:
    s = _strip_inner_ws(raw)
    if not s:
        return False
    if qtype == "judge":
        return s in JUDGE_TRUE | JUDGE_FALSE
    if qtype in {"single", "multi"}:
        if not ANSWER_LETTERS.match(s):
            return False
        if qtype == "single":
            return len(s) == 1
        return 2 <= len(s) <= 7
    return False


def _parse_answer(raw: str, qtype: str) -> list[str]:
    s = _strip_inner_ws(raw)
    if qtype == "judge":
        if s in JUDGE_TRUE:
            return ["T"]
        if s in JUDGE_FALSE:
            return ["F"]
        return []
    if qtype in {"single", "multi"}:
        return list(s) if ANSWER_LETTERS.match(s) else []
    return []


def parse_row(row: list, source: str, page_num: int, table_idx: int, row_idx: int) -> Optional[dict]:
    if not row or all(c is None or not str(c).strip() for c in row):
        return None
    cells = [_clean_cell(c) for c in row]

    qtype_raw = _strip_inner_ws(cells[0]) if cells else ""
    if qtype_raw in QTYPE_HEADERS:
        return None
    if qtype_raw not in QTYPES_RAW:
        return None
    qtype = QTYPE_NORM[qtype_raw]

    if len(cells) < 3:
        return None
    qid_raw = _strip_inner_ws(cells[1])
    if not qid_raw.isdigit():
        return None
    qid_int = int(qid_raw)

    stem = cells[2]
    if not stem:
        return None

    options: list[dict] = []
    for i in range(3, min(8, len(cells))):
        text = cells[i]
        if text:
            options.append({"key": chr(ord("A") + i - 3), "text": text})

    answer_raw = ""
    answer_col = -1
    candidate_cols = [8, 9] + list(range(len(cells) - 1, 7, -1))
    seen_cols: set[int] = set()
    for col_idx in candidate_cols:
        if col_idx in seen_cols or not 0 <= col_idx < len(cells):
            continue
        seen_cols.add(col_idx)
        cand = cells[col_idx]
        if cand and _is_plausible_answer(cand, qtype):
            answer_raw = cand
            answer_col = col_idx
            break

    if not answer_raw:
        return None

    answer = _parse_answer(answer_raw, qtype)
    if not answer:
        return None

    if qtype == "judge":
        options = []

    flags: list[str] = []
    if qtype == "single" and len(options) < 2:
        flags.append("single_too_few_options")
    if qtype == "multi" and len(options) < 2:
        flags.append("multi_too_few_options")
    if qtype in {"single", "multi"} and len(options) > 5:
        flags.append("too_many_options")
    for letter in answer:
        if qtype in {"single", "multi"} and ord(letter) - ord("A") >= len(options):
            flags.append("answer_out_of_options")
            break

    return {
        "qid": "",
        "source": source,
        "source_qid": qid_raw,
        "source_qid_int": qid_int,
        "page": page_num,
        "table_idx": table_idx,
        "row_idx": row_idx,
        "type": qtype,
        "type_raw": qtype_raw,
        "stem_raw": stem,
        "options_raw": options,
        "answer": answer,
        "answer_text_raw": answer_raw,
        "answer_col": answer_col,
        "flags": flags,
    }


def parse_pdf(pdf_path: Path, source: str) -> tuple[list[dict], dict]:
    questions: list[dict] = []
    page_stats: list[dict] = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables()
            n_rows = 0
            n_parsed = 0
            for ti, table in enumerate(tables):
                for ri, row in enumerate(table):
                    n_rows += 1
                    q = parse_row(row, source, page_num, ti, ri)
                    if q is not None:
                        questions.append(q)
                        n_parsed += 1
            page_stats.append(
                {
                    "page": page_num,
                    "tables": len(tables),
                    "rows": n_rows,
                    "parsed": n_parsed,
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
        "total_pages": total_pages,
        "pages_with_tables": sum(1 for s in page_stats if s["tables"]),
        "pages_no_tables": sum(1 for s in page_stats if not s["tables"]),
        "questions_parsed": len(questions),
        "min_qid": min(qids) if qids else 0,
        "max_qid": max_qid,
        "duplicate_qids": duplicates,
        "missing_qids": gaps,
        "judge": sum(1 for q in questions if q["type"] == "judge"),
        "single": sum(1 for q in questions if q["type"] == "single"),
        "multi": sum(1 for q in questions if q["type"] == "multi"),
        "flagged": sum(1 for q in questions if q["flags"]),
    }
    return questions, summary
