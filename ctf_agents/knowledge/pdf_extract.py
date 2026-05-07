from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def extract_with_pymupdf(pdf: Path) -> list[dict]:
    import fitz
    doc = fitz.open(pdf)
    return [{"page": i, "text": page.get_text("text")} for i, page in enumerate(doc, start=1)]

def extract_with_pdfplumber(pdf: Path) -> list[dict]:
    import pdfplumber
    with pdfplumber.open(pdf) as p:
        return [{"page": i, "text": page.extract_text() or ""} for i, page in enumerate(p.pages, start=1)]

def main() -> None:
    ap = argparse.ArgumentParser(description="本地抽取 PDF 文本，输出 pages.jsonl 和 all.txt")
    ap.add_argument("pdf", type=Path); ap.add_argument("--out", type=Path, default=Path("data/processed/pdf_extract")); args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        pages, engine = extract_with_pymupdf(args.pdf), "pymupdf"
    except Exception:
        pages, engine = extract_with_pdfplumber(args.pdf), "pdfplumber"
    meta = {"pdf_path": str(args.pdf), "sha256": sha256_file(args.pdf), "engine": engine, "pages": len(pages)}
    (args.out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.out / "pages.jsonl").open("w", encoding="utf-8") as f:
        for p in pages: f.write(json.dumps(p, ensure_ascii=False) + "\n")
    (args.out / "all.txt").write_text("\n\n".join(f"[PAGE {p['page']}]\n{p['text']}" for p in pages), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
if __name__ == "__main__": main()
