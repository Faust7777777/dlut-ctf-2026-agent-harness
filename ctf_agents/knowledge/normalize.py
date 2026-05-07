"""Text normalization for stem and option matching.

Two operations:
  normalize_text(s)        — collapse to a comparison-friendly form
  detect_negation(stem)    — flag questions whose semantics flip on a
                              negation keyword. Used as a guard against
                              fuzzy-matching a positive-phrased stem to
                              its negative-phrased twin (or vice versa).
"""
from __future__ import annotations

import re
import unicodedata


_PUNCT_MAP = str.maketrans(
    {
        "，": ",", "。": ".", "！": "!", "？": "?", "；": ";", "：": ":",
        "（": "(", "）": ")", "【": "[", "】": "]", "「": '"', "」": '"',
        "『": '"', "』": '"', "、": ",", "·": ".",
        "—": "-", "–": "-",
        " ": "", "　": "", "\xa0": "", "\t": "", "\n": "", "\r": "",
    }
)

_PARENTHETICAL_NOTE = re.compile(r"[\(（][^)）]{0,20}[\)）]")
_FILLER_BLANK = re.compile(r"[（\(]\s*[）\)]")
_REDUNDANT_PUNCT = re.compile(r"[,.;:!?\-]{2,}")


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _FILLER_BLANK.sub("()", s)
    s = s.translate(_PUNCT_MAP)
    s = _REDUNDANT_PUNCT.sub("", s)
    s = re.sub(r"[​-‏﻿]", "", s)
    s = s.lower()
    return s.strip()


_NEGATION_TOKENS = (
    "不属于", "不包括", "不正确", "不能", "不是", "不可", "不会", "不应",
    "不得", "不需要", "无关", "除外", "除...外", "除……外",
    "错误的", "错误是", "下列错误", "下列不正确", "以下错误",
    "非", "并非",
)
_NEGATION_RE = re.compile("|".join(re.escape(t) for t in _NEGATION_TOKENS))


def detect_negation(stem: str) -> bool:
    if not stem:
        return False
    s = stem.replace(" ", "")
    return bool(_NEGATION_RE.search(s))


def normalize_options(options: list[dict]) -> list[dict]:
    return [
        {**opt, "text_norm": normalize_text(opt.get("text", ""))}
        for opt in options
    ]
