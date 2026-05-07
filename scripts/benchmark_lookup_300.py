#!/usr/bin/env python3
"""300-question knowledge-race lookup throughput benchmark.

Picks 300 stratified questions from ``question_bank_merged.json``,
shuffles each one's options the way a real Wenjuanxing page would, and
runs the same code path that ``wjx_exam_assist.js`` triggers via
``/lookup_v2``: stem fuzzy match → option remap → answer letter(s).

The contest budget is 60 minutes for 300 questions = 12 s/question.
This benchmark measures the **server-side** floor (no browser, no
clicks); the gap to realistic per-question time is the chromium DOM
overhead which Phase 2 measures separately.

Outputs:
  - per-branch / overall aggregate to stdout
  - logs/benchmark-300-<ts>.jsonl with per-question timing
  - logs/benchmark-300-<ts>-summary.json with rollups

Usage:
  python scripts/benchmark_lookup_300.py                  # default 300, seed 7
  python scripts/benchmark_lookup_300.py --n 100 --seed 1
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.knowledge.lookup_engine import LookupEngine  # noqa: E402


def pick_stratified(bank: list[dict], n: int, rng: random.Random) -> list[dict]:
    by_type = {"judge": [], "single": [], "multi": []}
    for q in bank:
        if q.get("type") in by_type and q.get("stem_raw") and q.get("answer"):
            if q["type"] == "judge":
                by_type["judge"].append(q)
            elif q["type"] in ("single", "multi") and len(q.get("options_raw", [])) >= 3:
                by_type[q["type"]].append(q)
    # Mirror BJDCTF-ish distribution: judge≈40%, single≈40%, multi≈20%
    targets = {
        "judge": int(round(n * 0.40)),
        "single": int(round(n * 0.40)),
        "multi": n - int(round(n * 0.40)) - int(round(n * 0.40)),
    }
    picked: list[dict] = []
    for t, target in targets.items():
        pool = by_type[t]
        if len(pool) < target:
            target = len(pool)
        picked.extend(rng.sample(pool, target))
    rng.shuffle(picked)
    return picked


def shuffle_options(question: dict, rng: random.Random) -> tuple[list[str], list[str]]:
    """Return (page_option_texts, expected_page_letters)."""
    if question["type"] == "judge":
        return [], []
    options = list(question.get("options_raw", []))
    indices = list(range(len(options)))
    rng.shuffle(indices)
    page_texts = [options[i]["text"] for i in indices]
    answer_letters = set(question.get("answer", []))
    expected = []
    for new_pos, orig_idx in enumerate(indices):
        orig_letter = options[orig_idx]["key"]
        if orig_letter in answer_letters:
            expected.append(chr(ord("A") + new_pos))
    return page_texts, sorted(expected)


def expected_judge_label(question: dict) -> str:
    ans = question.get("answer", [])
    if not ans:
        return ""
    return "正确" if ans[0] == "T" else "错误"


def run_one(engine: LookupEngine, q: dict, rng: random.Random) -> dict:
    page_options, expected_letters = shuffle_options(q, rng)
    t0 = time.perf_counter()
    result = engine.lookup(q["stem_raw"], page_options if page_options else None)
    dt_ms = (time.perf_counter() - t0) * 1000.0

    correct = False
    if q["type"] == "judge":
        expected_label = expected_judge_label(q)
        # engine emits canonical "正确"/"错误"; compare directly
        if result.matched and result.answer_label == expected_label:
            correct = True
    else:
        if result.matched and sorted(result.answer_letters) == sorted(expected_letters):
            correct = True

    return {
        "qid": q.get("qid"),
        "type": q["type"],
        "stem_excerpt": q["stem_raw"][:50],
        "elapsed_ms": dt_ms,
        "matched": result.matched,
        "stem_score": result.stem_score,
        "expected": (
            sorted(expected_letters)
            if q["type"] != "judge"
            else expected_judge_label(q)
        ),
        "got": (
            sorted(result.answer_letters)
            if q["type"] != "judge"
            else result.answer_label
        ),
        "correct": correct,
        "notes": result.notes,
    }


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = int(round(p * (len(s) - 1)))
    return s[k]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--bank",
        default=str(PROJECT / "data" / "processed" / "question_bank_merged.json"),
    )
    args = ap.parse_args()

    bank_path = Path(args.bank)
    print(f"loading bank: {bank_path}")
    t_load = time.perf_counter()
    engine = LookupEngine(bank_path)
    load_ms = (time.perf_counter() - t_load) * 1000
    print(f"  loaded {len(engine._questions)} questions in {load_ms:.0f} ms\n")

    rng = random.Random(args.seed)
    sample = pick_stratified(engine._questions, args.n, rng)
    print(f"sampled {len(sample)} questions (seed={args.seed})")
    counter = Counter(q["type"] for q in sample)
    for t in ("judge", "single", "multi"):
        print(f"  {t:6s}: {counter.get(t, 0)}")
    print()

    run_id = datetime.now().strftime("benchmark-300-%Y%m%d-%H%M%S")
    log_path = PROJECT / "logs" / f"{run_id}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    t0 = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as f:
        for q in sample:
            r = run_one(engine, q, rng)
            results.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    total_ms = (time.perf_counter() - t0) * 1000

    times = [r["elapsed_ms"] for r in results]
    correct_n = sum(1 for r in results if r["correct"])
    by_type: dict[str, list[float]] = {"judge": [], "single": [], "multi": []}
    by_type_correct: dict[str, int] = {"judge": 0, "single": 0, "multi": 0}
    by_type_total: dict[str, int] = {"judge": 0, "single": 0, "multi": 0}
    for r in results:
        t = r["type"]
        by_type[t].append(r["elapsed_ms"])
        by_type_total[t] += 1
        if r["correct"]:
            by_type_correct[t] += 1

    print(f"=== latency ms (per-question) ===")
    print(f"  count    {len(times)}")
    print(f"  mean     {statistics.mean(times):.2f}")
    print(f"  median   {statistics.median(times):.2f}")
    print(f"  p90      {percentile(times, 0.90):.2f}")
    print(f"  p99      {percentile(times, 0.99):.2f}")
    print(f"  max      {max(times):.2f}")
    print(f"  total    {total_ms:.0f} ms = {total_ms/1000:.2f} s")
    print(f"  budget   60_000 ms / {len(times)} = {60_000/len(times):.0f} ms/题")
    print()

    print(f"=== accuracy ===")
    print(f"  overall      {correct_n}/{len(results)}  ({100*correct_n/len(results):.1f}%)")
    for t in ("judge", "single", "multi"):
        if by_type_total[t]:
            acc = 100 * by_type_correct[t] / by_type_total[t]
            mean = statistics.mean(by_type[t]) if by_type[t] else 0
            print(
                f"  {t:6s}      {by_type_correct[t]}/{by_type_total[t]}"
                f"  ({acc:.1f}%)  mean_lat={mean:.2f} ms"
            )
    print()

    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(results),
        "seed": args.seed,
        "load_ms": load_ms,
        "total_ms": total_ms,
        "latency_ms": {
            "mean": statistics.mean(times),
            "median": statistics.median(times),
            "p90": percentile(times, 0.90),
            "p99": percentile(times, 0.99),
            "max": max(times),
        },
        "accuracy": {
            "overall": correct_n / len(results),
            "judge": by_type_correct["judge"] / max(1, by_type_total["judge"]),
            "single": by_type_correct["single"] / max(1, by_type_total["single"]),
            "multi": by_type_correct["multi"] / max(1, by_type_total["multi"]),
        },
        "counts": dict(counter),
        "log_path": str(log_path),
    }
    summary_path = PROJECT / "logs" / f"{run_id}-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  log:     {log_path}")
    print(f"  summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
