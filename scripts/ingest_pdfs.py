#!/usr/bin/env python3
"""Ingest the 5 source PDFs: copy, hash, dual-track extract.

Each PDF gets a short name (used as qid namespace prefix later) and is
extracted twice in parallel (PyMuPDF + pdfplumber) to enable diffing.

Outputs:
  data/raw/<short>.pdf                  (copy of source)
  data/raw/META.json                    (summary across all 5)
  data/processed/<short>/extract_pymupdf/{pages.jsonl, all.txt, meta.json}
  data/processed/<short>/extract_pdfplumber/{pages.jsonl, all.txt, meta.json}
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pdfplumber

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(os.environ.get("DLUT_PDF_SOURCE_DIR", PROJECT / "data" / "raw_source"))

PDF_MAP = [
    ("2020-content",    "2020全国网络与信息安全管理职业技能大赛初赛题库（样题）《互联网内容安全管理》.pdf"),
    ("2020-tech",       "2020全国网络与信息安全管理职业技能大赛初赛题库（样题）《信息安全技术》.pdf"),
    ("2020-compliance", "2020全国网络与信息安全管理职业技能大赛初赛题库（样题）《网络安全合规指引》.pdf"),
    ("2020-practice",   "2020全国网络与信息安全管理职业技能大赛初赛题库（样题）《网络安全管理实践》.pdf"),
    ("2024-college",    "2024高校网络安全管理运维赛理论赛赛题.pdf"),
]


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pymupdf(p: Path) -> list[dict]:
    doc = fitz.open(p)
    pages = [{"page": i, "text": page.get_text("text")} for i, page in enumerate(doc, start=1)]
    doc.close()
    return pages


def extract_pdfplumber(p: Path) -> list[dict]:
    out = []
    with pdfplumber.open(p) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            out.append({"page": i, "text": page.extract_text() or ""})
    return out


def write_extract(pages: list[dict], outdir: Path, engine: str, src: Path, sha: str) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    with (outdir / "pages.jsonl").open("w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    (outdir / "all.txt").write_text(
        "\n\n".join(f"[PAGE {p['page']}]\n{p['text']}" for p in pages),
        encoding="utf-8",
    )
    total_chars = sum(len(p["text"]) for p in pages)
    blank_pages = sum(1 for p in pages if not p["text"].strip())
    meta = {
        "source_pdf": str(src),
        "sha256": sha,
        "engine": engine,
        "pages": len(pages),
        "total_chars": total_chars,
        "blank_pages": blank_pages,
        "avg_chars_per_page": round(total_chars / max(len(pages), 1), 1),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    (outdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def looks_scanned(meta: dict) -> bool:
    if "error" in meta:
        return False
    return meta["avg_chars_per_page"] < 30


def main() -> None:
    raw_dir = PROJECT / "data" / "raw"
    proc_dir = PROJECT / "data" / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    proc_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for short, fname in PDF_MAP:
        src = SOURCE_DIR / fname
        if not src.exists():
            print(f"[MISS] {short}: {src}")
            summary.append({"short": short, "filename": fname, "error": "source missing"})
            continue

        dst = raw_dir / f"{short}.pdf"
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            sha_dst = sha256_file(dst)
            sha_src = sha256_file(src)
            if sha_dst == sha_src:
                print(f"[SKIP-COPY] {short}: identical copy already at {dst.name}")
                sha = sha_dst
            else:
                shutil.copy2(src, dst)
                sha = sha256_file(dst)
                print(f"[REFRESH]   {short}: {dst.name} ({dst.stat().st_size} B)")
        else:
            shutil.copy2(src, dst)
            sha = sha256_file(dst)
            print(f"[COPY]      {short}: {dst.name} ({dst.stat().st_size} B)")

        py_dir = proc_dir / short / "extract_pymupdf"
        try:
            pages = extract_pymupdf(dst)
            meta_py = write_extract(pages, py_dir, "pymupdf", dst, sha)
            print(
                f"  [PyMuPDF]   pages={meta_py['pages']:3d}  chars={meta_py['total_chars']:>7}"
                f"  blank={meta_py['blank_pages']:2d}  avg={meta_py['avg_chars_per_page']}"
            )
        except Exception as e:
            meta_py = {"engine": "pymupdf", "error": str(e)}
            print(f"  [PyMuPDF FAIL] {e}")

        pl_dir = proc_dir / short / "extract_pdfplumber"
        try:
            pages = extract_pdfplumber(dst)
            meta_pl = write_extract(pages, pl_dir, "pdfplumber", dst, sha)
            print(
                f"  [pdfplumb]  pages={meta_pl['pages']:3d}  chars={meta_pl['total_chars']:>7}"
                f"  blank={meta_pl['blank_pages']:2d}  avg={meta_pl['avg_chars_per_page']}"
            )
        except Exception as e:
            meta_pl = {"engine": "pdfplumber", "error": str(e)}
            print(f"  [pdfplumber FAIL] {e}")

        verdict = "scanned" if looks_scanned(meta_py) and looks_scanned(meta_pl) else "text"
        print(f"  [verdict]   {verdict}")

        summary.append({
            "short": short,
            "filename": fname,
            "sha256": sha,
            "size_bytes": dst.stat().st_size,
            "verdict": verdict,
            "pymupdf": meta_py,
            "pdfplumber": meta_pl,
        })

    meta_path = raw_dir / "META.json"
    meta_path.write_text(
        json.dumps(
            {
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "source_dir": str(SOURCE_DIR),
                "files": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[META] {meta_path}")


if __name__ == "__main__":
    main()
