"""Three-branch lookup engine: judge / single / multi.

The bank produced by ``build_all_banks.py`` mixes all 5 PDFs.  At lookup
time the user pastes the contest page's stem (and current option order
for single/multi).  This engine returns the answer letter(s) that map to
the *current* page.

Branch semantics (matches §3 of the handoff plan):

* judge  — return T/F mapped to the platform-visible label.
* single — fuzzy-match the bank's correct option text to the current
           options; emit warnings if the next-best option is too close.
* multi  — greedy assignment of every bank-correct option to a current
           option; emit a strong warning whenever the bank's correct
           count cannot be fully accounted for (no silent intersection
           truncation).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

from .normalize import detect_negation, normalize_options, normalize_text


JUDGE_LABELS_TRUE = ("正确", "对", "T", "True", "✓", "是")
JUDGE_LABELS_FALSE = ("错误", "错", "F", "False", "✗", "否")


@dataclass
class StemCandidate:
    qid: str
    score: float
    bank_question: dict


@dataclass
class OptionMatch:
    bank_text: str
    page_letter: Optional[str]
    page_text: Optional[str]
    score: float


@dataclass
class LookupResult:
    matched: bool = False
    qid: Optional[str] = None
    branch: Optional[str] = None
    stem_score: float = 0.0
    answer_letters: list[str] = field(default_factory=list)
    answer_label: Optional[str] = None
    bank_answer_texts: list[str] = field(default_factory=list)
    option_matches: list[OptionMatch] = field(default_factory=list)
    candidates: list[StemCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    bank_question: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "qid": self.qid,
            "branch": self.branch,
            "stem_score": self.stem_score,
            "answer_letters": self.answer_letters,
            "answer_label": self.answer_label,
            "bank_answer_texts": self.bank_answer_texts,
            "option_matches": [
                {
                    "bank_text": m.bank_text,
                    "page_letter": m.page_letter,
                    "page_text": m.page_text,
                    "score": m.score,
                }
                for m in self.option_matches
            ],
            "candidates": [
                {"qid": c.qid, "score": c.score, "stem": c.bank_question.get("stem_raw", "")[:80]}
                for c in self.candidates
            ],
            "notes": self.notes,
            "elapsed_ms": self.elapsed_ms,
        }


class LookupEngine:
    def __init__(self, bank_path: Path, *, top_k: int = 5):
        self.bank_path = bank_path
        self.top_k = top_k
        self._questions: list[dict] = []
        self._stems_norm: list[str] = []
        self._load()

    def _load(self) -> None:
        with self.bank_path.open(encoding="utf-8") as f:
            bank = json.load(f)
        self._questions = bank.get("questions", [])
        for q in self._questions:
            stem = q.get("stem_raw", "")
            q["stem_norm"] = normalize_text(stem)
            q["negation"] = detect_negation(stem)
            q["options_norm"] = normalize_options(q.get("options_raw", []))
            q["answer_texts_norm"] = self._derive_answer_texts(q)
        self._stems_norm = [q["stem_norm"] for q in self._questions]

    @staticmethod
    def _derive_answer_texts(q: dict) -> list[str]:
        if q["type"] == "judge":
            return []
        letters = set(q.get("answer", []))
        out: list[str] = []
        for opt in q.get("options_raw", []):
            if opt["key"] in letters:
                out.append(normalize_text(opt["text"]))
        return out

    def search_stem(self, query: str, top_k: Optional[int] = None) -> list[StemCandidate]:
        k = top_k if top_k is not None else self.top_k
        q_norm = normalize_text(query)
        if not q_norm:
            return []
        results = process.extract(
            q_norm,
            self._stems_norm,
            scorer=fuzz.token_set_ratio,
            limit=k,
        )
        out: list[StemCandidate] = []
        for stem_norm, score, idx in results:
            q = self._questions[idx]
            out.append(StemCandidate(qid=q["qid"], score=float(score), bank_question=q))
        return out

    def lookup(
        self,
        stem: str,
        current_options: Optional[list[str]] = None,
    ) -> LookupResult:
        t0 = time.perf_counter()
        result = LookupResult()

        candidates = self.search_stem(stem)
        result.candidates = candidates

        if not candidates:
            result.notes.append("no_match")
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            return result

        top = candidates[0]
        result.qid = top.qid
        result.stem_score = top.score
        result.bank_question = top.bank_question

        bank_q = top.bank_question
        result.branch = bank_q["type"]
        self._propagate_review_flags(bank_q, result)

        if top.score < 82:
            result.notes.append("low_stem_score")
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            return result
        if top.score < 92 and len(candidates) > 1:
            second = candidates[1]
            if second.score >= top.score - 6:
                result.notes.append("close_second_candidate")

        query_neg = detect_negation(stem)
        if query_neg != bank_q.get("negation", False):
            result.notes.append("negation_mismatch")

        if bank_q["type"] == "judge":
            self._resolve_judge(bank_q, result)
        elif bank_q["type"] == "single":
            self._resolve_single(bank_q, current_options, result)
        elif bank_q["type"] == "multi":
            self._resolve_multi(bank_q, current_options, result)
        else:
            result.notes.append(f"unknown_type:{bank_q['type']}")

        result.matched = "no_match" not in result.notes
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        return result

    @staticmethod
    def _propagate_review_flags(bank_q: dict, result: LookupResult) -> None:
        for flag in bank_q.get("flags", []):
            if str(flag).startswith("manual_review_required:") and flag not in result.notes:
                result.notes.append(flag)

    @staticmethod
    def _resolve_judge(bank_q: dict, result: LookupResult) -> None:
        ans = bank_q.get("answer", [])
        if not ans:
            result.notes.append("judge_no_answer")
            return
        token = ans[0]
        if token == "T":
            result.answer_label = JUDGE_LABELS_TRUE[0]
        elif token == "F":
            result.answer_label = JUDGE_LABELS_FALSE[0]
        else:
            result.notes.append(f"judge_unknown_token:{token}")
            return
        result.bank_answer_texts = [result.answer_label]

    def _resolve_single(
        self,
        bank_q: dict,
        current_options: Optional[list[str]],
        result: LookupResult,
    ) -> None:
        ans_letters = bank_q.get("answer", [])
        if not ans_letters:
            result.notes.append("single_no_answer")
            return
        bank_correct_text = ""
        for opt in bank_q.get("options_raw", []):
            if opt["key"] == ans_letters[0]:
                bank_correct_text = opt["text"]
                break
        result.bank_answer_texts = [bank_correct_text]

        if not current_options:
            result.answer_letters = ans_letters
            result.notes.append("no_current_options_pdf_letter_only")
            return

        norm_target = normalize_text(bank_correct_text)
        scored: list[tuple[int, float]] = []
        for i, page_opt in enumerate(current_options):
            score = fuzz.token_set_ratio(norm_target, normalize_text(page_opt))
            scored.append((i, score))
        scored.sort(key=lambda x: -x[1])
        best_i, best_score = scored[0]
        result.option_matches.append(
            OptionMatch(
                bank_text=bank_correct_text,
                page_letter=chr(ord("A") + best_i),
                page_text=current_options[best_i],
                score=float(best_score),
            )
        )
        if best_score < 85:
            result.notes.append("single_option_low_score")
            return
        if len(scored) >= 2 and best_score - scored[1][1] < 15:
            result.notes.append("single_option_close_second")
        result.answer_letters = [chr(ord("A") + best_i)]

    def _resolve_multi(
        self,
        bank_q: dict,
        current_options: Optional[list[str]],
        result: LookupResult,
    ) -> None:
        ans_letters = bank_q.get("answer", [])
        bank_correct_texts: list[str] = []
        for opt in bank_q.get("options_raw", []):
            if opt["key"] in ans_letters:
                bank_correct_texts.append(opt["text"])
        result.bank_answer_texts = bank_correct_texts

        if not bank_correct_texts:
            result.notes.append("multi_no_correct_options")
            return

        if not current_options:
            result.answer_letters = ans_letters
            result.notes.append("no_current_options_pdf_letters_only")
            return

        used: set[int] = set()
        matches: list[OptionMatch] = []
        for bank_text in bank_correct_texts:
            norm_target = normalize_text(bank_text)
            best: tuple[int, float] = (-1, -1.0)
            for i, page_opt in enumerate(current_options):
                if i in used:
                    continue
                score = fuzz.token_set_ratio(norm_target, normalize_text(page_opt))
                if score > best[1]:
                    best = (i, score)
            if best[0] == -1:
                matches.append(
                    OptionMatch(bank_text=bank_text, page_letter=None, page_text=None, score=0.0)
                )
                continue
            page_letter = chr(ord("A") + best[0])
            page_text = current_options[best[0]]
            matches.append(
                OptionMatch(
                    bank_text=bank_text,
                    page_letter=page_letter,
                    page_text=page_text,
                    score=float(best[1]),
                )
            )
            if best[1] >= 85:
                used.add(best[0])

        result.option_matches = matches
        confirmed_letters = sorted(
            m.page_letter for m in matches if m.page_letter and m.score >= 85
        )

        n_bank = len(bank_correct_texts)
        n_confirmed = len(confirmed_letters)
        if n_confirmed == n_bank and n_confirmed > 0:
            result.answer_letters = confirmed_letters
        elif n_confirmed > 0 and n_confirmed < n_bank:
            result.notes.append(
                f"multi_partial_match:{n_confirmed}/{n_bank}_no_silent_intersection"
            )
        else:
            result.notes.append("multi_match_failed")
