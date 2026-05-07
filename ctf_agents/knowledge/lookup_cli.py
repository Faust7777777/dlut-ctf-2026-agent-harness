"""Interactive contest-day lookup CLI (LookupEngine front-end).

Usage:
    python -m ctf_agents.knowledge.lookup_cli                     # interactive
    python -m ctf_agents.knowledge.lookup_cli --bank PATH         # custom bank
    python -m ctf_agents.knowledge.lookup_cli --once "题干..."    # one-shot

Interactive flow per question:
    1. Paste the stem (one line).
    2. If single/multi: paste options A/B/C/D… in current page order, one
       per line.  Empty line ends the option list.  For 判断题 just press
       Enter at the first option prompt.
    3. The CLI prints answer + branch + notes; press Enter to continue,
       ``?`` to see top-3 candidates, ``q`` to quit.

Every query is appended to ``logs/lookup-YYYYMMDD.jsonl`` for post-game
review and writeup.  Webhook URLs / cookies are never logged here.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .lookup_engine import LookupEngine, LookupResult


PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_BANK = PROJECT / "data" / "processed" / "question_bank_merged.json"
LOOKUP_LOG_DIR = PROJECT / "logs"


def _read_options() -> list[str]:
    print("  粘贴当前页面选项（按 A B C D 顺序，每行一个；判断题留空回车）：")
    options: list[str] = []
    while True:
        try:
            line = input(f"    {chr(ord('A') + len(options))}. ")
        except EOFError:
            break
        if not line.strip():
            break
        options.append(line.strip())
        if len(options) >= 7:
            break
    return options


def _summary_line(result: LookupResult) -> str:
    if not result.matched:
        return "  ✗ 未命中（请人工查 PDF）"
    branch = result.branch
    if branch == "judge":
        return f"  → 应选「{result.answer_label}」  (题干 score {result.stem_score:.0f})"
    if branch == "single":
        if result.answer_letters:
            letter = result.answer_letters[0]
            return f"  → 应选 {letter}  (题干 score {result.stem_score:.0f})"
        return f"  → 题干已命中但选项映射失败  (题干 score {result.stem_score:.0f})"
    if branch == "multi":
        if result.answer_letters:
            letters = "".join(result.answer_letters)
            return (
                f"  → 应选 {letters}  "
                f"(题库 {len(result.bank_answer_texts)} 个正确选项全映射成功)"
            )
        return (
            f"  ⚠ 多选映射不完整：题库有 {len(result.bank_answer_texts)} 个正确选项，"
            f"请按下方提示人工选"
        )
    return "  ?"


def _print_result(result: LookupResult) -> None:
    print()
    print(_summary_line(result))
    if result.bank_answer_texts:
        print(f"  题库正确选项文本：")
        for t in result.bank_answer_texts:
            print(f"    • {t}")
    if result.option_matches:
        print(f"  选项映射：")
        for m in result.option_matches:
            page = m.page_letter or "<无匹配>"
            page_txt = (m.page_text or "")[:40]
            print(
                f"    {page}  '{page_txt}'  ← 题库 '{m.bank_text[:30]}' (score {m.score:.0f})"
            )
    if result.notes:
        print(f"  notes: {result.notes}")
    print(f"  耗时 {result.elapsed_ms:.1f} ms  qid={result.qid}")


def _format_top_candidates(result: LookupResult, n: int = 3) -> str:
    lines = []
    for i, c in enumerate(result.candidates[:n], start=1):
        lines.append(
            f"    #{i} score={c.score:5.1f}  qid={c.qid}  stem={c.bank_question['stem_raw'][:60]}"
        )
    return "\n".join(lines)


def _log_query(log_path: Path, stem: str, options: list[str], result: LookupResult) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stem": stem,
        "options": options,
        "result": result.to_dict(),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def interactive_loop(engine: LookupEngine, log_path: Path) -> None:
    seen = 0
    print("--- DLUT 知识赛 lookup CLI ---")
    print("提示：粘贴题干（一行）。判断题在选项处按空行回车。")
    print("命令：q 退出。每题结束按 ? 看 top3 候选。\n")
    while True:
        try:
            stem = input(f"[Q{seen + 1}] 题干（输入 q 退出）> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        stem = stem.strip()
        if stem in ("q", "Q", "quit", "exit"):
            break
        if not stem:
            continue
        seen += 1
        options = _read_options()
        result = engine.lookup(stem, options if options else None)
        _print_result(result)
        _log_query(log_path, stem, options, result)

        try:
            cmd = input("\n[回车继续 / ? 看 top3 / q 退出]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if cmd == "?":
            print("  top 3 候选：")
            print(_format_top_candidates(result, 3))
            try:
                input("[回车继续]: ")
            except (EOFError, KeyboardInterrupt):
                break
        elif cmd in ("q", "Q", "quit", "exit"):
            break
        print()
    print(f"\n--- 退出，本次共查询 {seen} 题 ---")


def one_shot(engine: LookupEngine, stem: str, options: list[str]) -> int:
    result = engine.lookup(stem, options if options else None)
    _print_result(result)
    return 0 if result.matched else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", type=Path, default=DEFAULT_BANK)
    ap.add_argument("--once", help="单次查询模式：题干")
    ap.add_argument("--option", action="append", default=[], help="--once 配合：A/B/C/D 选项文本")
    ap.add_argument(
        "--log",
        type=Path,
        default=LOOKUP_LOG_DIR / f"lookup-{time.strftime('%Y%m%d')}.jsonl",
    )
    args = ap.parse_args()

    if not args.bank.exists():
        print(f"bank 文件不存在：{args.bank}", file=sys.stderr)
        return 2
    print(f"loading {args.bank} ...", file=sys.stderr)
    t0 = time.perf_counter()
    engine = LookupEngine(args.bank)
    print(
        f"  loaded {len(engine._questions)} questions in {(time.perf_counter()-t0)*1000:.0f} ms",
        file=sys.stderr,
    )

    if args.once:
        return one_shot(engine, args.once, args.option)
    interactive_loop(engine, args.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
