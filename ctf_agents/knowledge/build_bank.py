from __future__ import annotations
import argparse, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
QUESTION_SPLIT = re.compile(r"(?m)^\s*(\d{1,4})[\.．、\)]\s*")
OPTION_RE = re.compile(r"(?m)^\s*([A-F])[\.．、\)]\s*(.+?)(?=\n\s*[A-F][\.．、\)]|\n\s*答案\s*[:：]|\Z)", re.S)
ANSWER_RE = re.compile(r"答案\s*[:：]\s*([A-F]+|正确|错误|对|错|√|×|T|F|True|False)", re.I)

def norm(s: str) -> str:
    s = re.sub(r"\s+", "", s).replace("（", "(").replace("）", ")").replace("，", ",").replace("。", ".").replace("：", ":")
    return s.lower()

def infer_type(options: list[dict[str, str]], answer: list[str]) -> str:
    keys = {o["key"].upper() for o in options}
    joined = "".join(o.get("text", "") for o in options) + "".join(answer)
    if (not options or keys <= {"A", "B"}) and any(x in joined for x in ["正确", "错误", "对", "错", "True", "False", "√", "×", "T", "F"]): return "true_false"
    if len(answer) > 1: return "multiple"
    if len(answer) == 1 and answer[0] in list("ABCDEF"): return "single"
    return "unknown"

def parse_blocks(text: str) -> list[dict[str, Any]]:
    matches = list(QUESTION_SPLIT.finditer(text)); questions = []
    for idx, m in enumerate(matches):
        qid = m.group(1); start = m.end(); end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text); block = text[start:end].strip()
        ans_m = ANSWER_RE.search(block); ans_raw = ans_m.group(1).strip() if ans_m else ""; ans = []
        if ans_raw:
            low = ans_raw.lower()
            if low in {"正确", "对", "√", "t", "true"}: ans = ["T"]
            elif low in {"错误", "错", "×", "f", "false"}: ans = ["F"]
            else: ans = list(ans_raw.upper())
        body = block[:ans_m.start()].strip() if ans_m else block.strip()
        opts = [{"key": om.group(1).upper(), "text": re.sub(r"\s+", " ", om.group(2)).strip()} for om in OPTION_RE.finditer(body)]
        stem = OPTION_RE.sub("", body).strip()
        if not stem:
            lines = [x.strip() for x in body.splitlines() if x.strip()]; stem = lines[0] if lines else body[:120]
        questions.append({"qid": qid, "type": infer_type(opts, ans), "stem": re.sub(r"\s+", " ", stem).strip(), "stem_norm": norm(stem), "options": [{**o, "text_norm": norm(o["text"])} for o in opts], "answer": ans, "answer_text": ans_raw, "answer_source": {"page": -1, "line": -1, "raw": ans_m.group(0) if ans_m else ""}, "confidence": 0.95 if ans_m else 0.35, "tags": []})
    return questions

def main() -> None:
    ap = argparse.ArgumentParser(description="从抽取文本构建知识题库 JSON")
    ap.add_argument("text", type=Path); ap.add_argument("--pdf", type=Path, default=None); ap.add_argument("--out", type=Path, default=Path("data/processed/question_bank.json")); args = ap.parse_args()
    raw = args.text.read_text(encoding="utf-8", errors="ignore"); qs = parse_blocks(raw)
    src: dict[str, Any] = {"text_path": str(args.text), "parser_version": "heuristic-0.1"}
    if args.pdf and args.pdf.exists(): src.update({"pdf_path": str(args.pdf), "sha256": hashlib.sha256(args.pdf.read_bytes()).hexdigest()})
    bank = {"source": src, "generated_at": datetime.now(timezone.utc).isoformat(), "questions": qs}
    args.out.parent.mkdir(parents=True, exist_ok=True); args.out.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(args.out), "questions": len(qs), "low_confidence": sum(1 for q in qs if q["confidence"] < 0.8)}, ensure_ascii=False, indent=2))
if __name__ == "__main__": main()
