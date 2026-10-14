"""probe_12_pdf_extract.py — 用 pypdf 逐页抽取课件 PDF 文本（只读）

用 Anaconda 自带的 pypdf 运行，**不向 H:\\mm-agent\\.venv 安装任何包**：
    /h/Anaconda/python.exe probe_12_pdf_extract.py
输出：hitl.pdf.txt / graph07.pdf.txt（每页带 PAGE 标记）
"""
import sys
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent / "pdf"
TARGETS = [("hitl.pdf", "hitl"), ("graph07.pdf", "graph07")]

for fname, tag in TARGETS:
    path = HERE / fname
    reader = PdfReader(str(path))
    out = []
    out.append(f"===== {fname}  pages={len(reader.pages)} =====")
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            text = f"<extract_error: {type(exc).__name__}: {exc}>"
        out.append(f"\n----- PAGE {i} -----")
        out.append(text.strip())
    txt = "\n".join(out)
    dest = HERE / f"{tag}.pdf.txt"
    dest.write_text(txt, encoding="utf-8")
    print(f"[ok] {fname}: pages={len(reader.pages)} chars={len(txt)} -> {dest.name}")
