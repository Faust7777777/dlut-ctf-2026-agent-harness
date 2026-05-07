"""FastAPI lookup service.

Two endpoints:

  POST /lookup            — legacy stem-only top-k matches (kept for the
                            CLI tools in the original skeleton).
  POST /lookup_v2         — three-branch lookup with optional current
                            page options for shuffled-mapping; this is
                            what ``scripts/wjx_exam_assist.js`` calls.

Both routes share a single LookupEngine instance loaded at app start.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .build_bank import norm
from .lookup_engine import LookupEngine

try:
    from rapidfuzz import fuzz, process
except Exception:
    fuzz = process = None


class LookupRequest(BaseModel):
    text: str
    top_k: int = 5


class LookupV2Request(BaseModel):
    text: str
    options: Optional[list[str]] = None
    top_k: int = 5


class KnowledgeIndex:
    """Legacy thin index used by ``lookup_cli.py`` against the old
    per-PDF question_bank.json schema."""

    def __init__(self, bank_path: Path):
        self.bank = json.loads(bank_path.read_text(encoding="utf-8"))
        self.questions = self.bank.get("questions", [])
        self.choices = [
            q.get("stem_norm", norm(q.get("stem", ""))) for q in self.questions
        ]

    def lookup(self, text: str, top_k: int = 5) -> list[dict[str, Any]]:
        query = norm(text)
        if process is not None:
            matches = process.extract(query, self.choices, scorer=fuzz.WRatio, limit=top_k)
            pairs = [(score, idx) for _, score, idx in matches]
        else:
            pairs = sorted(
                [((100 if query in c or c in query else 0), i) for i, c in enumerate(self.choices)],
                reverse=True,
            )[:top_k]
        out = []
        for score, idx in pairs:
            q = self.questions[idx]
            out.append({
                "score": score,
                "qid": q.get("qid"),
                "type": q.get("type"),
                "stem": q.get("stem"),
                "options": q.get("options", []),
                "answer": q.get("answer", []),
                "answer_text": q.get("answer_text", ""),
                "confidence": min(1.0, (score / 100.0) * float(q.get("confidence", 0.8))),
            })
        return out


def create_app(bank_path: Path) -> FastAPI:
    legacy = KnowledgeIndex(bank_path)
    engine = LookupEngine(bank_path)

    app = FastAPI(title="Knowledge Lookup")

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "questions": len(engine._questions),
            "endpoints": ["/lookup", "/lookup_v2"],
        }

    @app.post("/lookup")
    def lookup(req: LookupRequest):
        return {"matches": legacy.lookup(req.text, req.top_k)}

    @app.post("/lookup_v2")
    def lookup_v2(req: LookupV2Request):
        result = engine.lookup(req.text, req.options)
        payload = result.to_dict()
        payload["candidates_count"] = len(result.candidates)
        return payload

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--bank",
        type=Path,
        default=Path("data/processed/question_bank_merged.json"),
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    import uvicorn

    uvicorn.run(create_app(args.bank), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
