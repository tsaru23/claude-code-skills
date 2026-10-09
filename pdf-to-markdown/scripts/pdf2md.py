#!/usr/bin/env python3
"""PDF を Markdown に一括変換する（PyMuPDF4LLM 使用、LLM は使わない）。

使い方:
  python pdf2md.py <入力ファイルまたはフォルダ> [-o 出力フォルダ] [--force] [--images]

- 入力がフォルダなら直下の *.pdf を全て変換する（-r で再帰）。
- 出力先の既定は、入力フォルダ直下の `md/`。
- 出力の .md が PDF より新しければスキップする（--force で再変換）。
- ページごとに `<!-- page N -->` を入れる。
- テキストがほぼ無いページ（スキャン・画像のみ）は警告し、末尾の一覧に出す。
  そのページは OCR（例: tesseract、PyMuPDF の OCR）に回す。LLM に読ませるのは最後の手段。
"""
import argparse
import sys
from pathlib import Path

import pymupdf
import pymupdf4llm

MIN_CHARS_PER_PAGE = 20


def convert(pdf: Path, out_dir: Path, force: bool, images: bool):
    out = out_dir / (pdf.stem.strip() + ".md")
    if out.exists() and not force and out.stat().st_mtime >= pdf.stat().st_mtime:
        return out, [], "skip"
    kwargs = {"page_chunks": True}
    if images:
        img_dir = out_dir / "images" / pdf.stem.strip()
        img_dir.mkdir(parents=True, exist_ok=True)
        kwargs.update(write_images=True, image_path=str(img_dir), image_format="png")
    chunks = pymupdf4llm.to_markdown(str(pdf), **kwargs)
    empty, parts = [], [f"# {pdf.stem.strip()}\n"]
    for i, c in enumerate(chunks, 1):
        text = c["text"].strip()
        if len(text) < MIN_CHARS_PER_PAGE:
            empty.append(i)
        parts.append(f"<!-- page {i} -->\n\n{text}\n")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out, empty, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("-r", "--recursive", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--images", action="store_true", help="埋め込み画像も PNG で書き出す")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if a.src.is_dir():
        pdfs = sorted(a.src.rglob("*.pdf") if a.recursive else a.src.glob("*.pdf"))
        out_dir = a.out or a.src / "md"
    else:
        pdfs, out_dir = [a.src], a.out or a.src.parent / "md"
    out_dir.mkdir(parents=True, exist_ok=True)

    needs_ocr = []
    for p in pdfs:
        try:
            out, empty, st = convert(p, out_dir, a.force, a.images)
        except Exception as e:  # 壊れた PDF 等
            print(f"FAIL  {p.name}: {e}")
            continue
        n = len(pymupdf.open(p)) if st == "ok" else "-"
        print(f"{st.upper():5} {p.name} -> {out.name} ({n} pages)")
        if empty:
            needs_ocr.append((p.name, empty))
    if needs_ocr:
        print("\n[要OCR/目視] テキストがほぼ無いページ:")
        for name, pages in needs_ocr:
            print(f"  {name}: {pages}")


if __name__ == "__main__":
    main()
