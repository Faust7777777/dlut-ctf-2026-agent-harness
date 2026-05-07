#!/usr/bin/env python3
"""Smoke test the LookupEngine across the three branches.

Pulls a sample of questions from the merged bank, simulates the user
pasting the stem (and current options for single/multi), and exercises:
  - judge: stem only
  - single: stem + original option order  → should match perfectly
  - single: stem + shuffled option order  → letter must follow shuffle
  - multi:  stem + original option order
  - multi:  stem + shuffled option order
  - stem with mild perturbation (whitespace, punctuation, parenthetical) → still match
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from ctf_agents.knowledge.lookup_engine import LookupEngine  # noqa: E402


BANK = PROJECT / "data" / "processed" / "question_bank_merged.json"


def pick_samples(bank: list[dict], rng: random.Random) -> dict:
    by_type: dict[str, list[dict]] = {"judge": [], "single": [], "multi": []}
    for q in bank:
        if q.get("flags"):
            continue
        if q["type"] in by_type and q.get("stem_raw") and q.get("answer"):
            if q["type"] in ("single", "multi") and len(q.get("options_raw", [])) >= 3:
                by_type[q["type"]].append(q)
            elif q["type"] == "judge":
                by_type[q["type"]].append(q)
    return {t: rng.sample(qs, min(2, len(qs))) for t, qs in by_type.items()}


def shuffle_options(options: list[dict], rng: random.Random) -> tuple[list[str], dict[int, str]]:
    indices = list(range(len(options)))
    rng.shuffle(indices)
    new_texts = [options[i]["text"] for i in indices]
    expected_letter_map = {orig_i: chr(ord("A") + new_pos) for new_pos, orig_i in enumerate(indices)}
    return new_texts, expected_letter_map


JUDGE_TRUE_LABELS = {"正确", "对", "T", "True", "✓", "是"}
JUDGE_FALSE_LABELS = {"错误", "错", "F", "False", "✗", "否"}


def show(label: str, result, expected: str = "") -> bool:
    status = "----"
    if result.branch == "judge":
        if expected == "T" and result.answer_label in JUDGE_TRUE_LABELS:
            status = "PASS"
        elif expected == "F" and result.answer_label in JUDGE_FALSE_LABELS:
            status = "PASS"
    elif expected and "".join(result.answer_letters) == expected:
        status = "PASS"
    if result.matched and not expected:
        status = "OK"
    if "no_match" in result.notes or not result.matched:
        status = "FAIL"
    print(f"  [{status}] {label}")
    print(f"    qid={result.qid} branch={result.branch} stem_score={result.stem_score:.1f}")
    print(f"    answer_letters={result.answer_letters} answer_label={result.answer_label}")
    if result.bank_answer_texts:
        previews = [t[:30] + ('…' if len(t) > 30 else '') for t in result.bank_answer_texts]
        print(f"    bank_answer_texts={previews}")
    if result.option_matches:
        for m in result.option_matches:
            print(f"      bank='{m.bank_text[:25]}' → page='{m.page_letter}': '{(m.page_text or '')[:25]}' (score={m.score:.0f})")
    if result.notes:
        print(f"    notes={result.notes}")
    print(f"    elapsed_ms={result.elapsed_ms:.1f}")
    return status in {"PASS", "OK"}


def main() -> None:
    print(f"loading bank from {BANK}")
    engine = LookupEngine(BANK)
    print(f"  loaded {len(engine._questions)} questions  index ready")
    rng = random.Random(42)
    bank = engine._questions
    samples = pick_samples(bank, rng)
    print(f"  samples: judge={len(samples['judge'])} single={len(samples['single'])} multi={len(samples['multi'])}")

    passed = 0
    total = 0

    print("\n=== JUDGE ===")
    for q in samples["judge"]:
        total += 1
        expected = q["answer"][0]
        r = engine.lookup(q["stem_raw"])
        ok = show(f"judge q={q['qid']} expected={expected}", r, expected)
        if ok:
            passed += 1

    print("\n=== SINGLE — original order ===")
    for q in samples["single"]:
        total += 1
        opts = [o["text"] for o in q["options_raw"]]
        expected_letter = q["answer"][0]
        r = engine.lookup(q["stem_raw"], opts)
        ok = show(f"single q={q['qid']} expected={expected_letter}", r, expected_letter)
        if ok:
            passed += 1

    print("\n=== SINGLE — shuffled order ===")
    for q in samples["single"]:
        total += 1
        new_texts, letter_map = shuffle_options(q["options_raw"], rng)
        bank_ans_letter = q["answer"][0]
        bank_ans_idx = ord(bank_ans_letter) - ord("A")
        expected_letter = letter_map[bank_ans_idx]
        r = engine.lookup(q["stem_raw"], new_texts)
        ok = show(f"single q={q['qid']} bank_letter={bank_ans_letter}→shuffled={expected_letter}", r, expected_letter)
        if ok:
            passed += 1

    print("\n=== MULTI — original order ===")
    for q in samples["multi"]:
        total += 1
        opts = [o["text"] for o in q["options_raw"]]
        expected = "".join(sorted(q["answer"]))
        r = engine.lookup(q["stem_raw"], opts)
        ok = show(f"multi q={q['qid']} expected={expected}", r, expected)
        if ok:
            passed += 1

    print("\n=== MULTI — shuffled order ===")
    for q in samples["multi"]:
        total += 1
        new_texts, letter_map = shuffle_options(q["options_raw"], rng)
        bank_letters = q["answer"]
        expected_set = sorted(letter_map[ord(L) - ord("A")] for L in bank_letters)
        expected = "".join(expected_set)
        r = engine.lookup(q["stem_raw"], new_texts)
        ok = show(f"multi q={q['qid']} bank={''.join(sorted(bank_letters))}→shuffled={expected}", r, expected)
        if ok:
            passed += 1

    print("\n=== STEM PERTURBATION ===")
    if samples["single"]:
        q = samples["single"][0]
        # Mild perturbations: whitespace, punctuation, parenthetical insertion
        perturbations = [
            ("space-strip", q["stem_raw"].replace(" ", "")),
            ("add-paren", q["stem_raw"] + "（请选择）"),
            ("punct-swap", q["stem_raw"].replace("。", ".").replace("，", ",")),
        ]
        for name, perturbed in perturbations:
            total += 1
            r = engine.lookup(perturbed, [o["text"] for o in q["options_raw"]])
            expected = q["answer"][0]
            ok = show(f"perturb={name} q={q['qid']}", r, expected)
            if ok:
                passed += 1

    print(f"\n=== SUMMARY: {passed}/{total} passed ===")


if __name__ == "__main__":
    main()
