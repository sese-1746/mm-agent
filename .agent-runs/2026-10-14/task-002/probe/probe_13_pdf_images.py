"""probe_13_pdf_images.py — 抽取课件 PDF 里"纯图片页"的图，便于人工/多模态阅读

背景：pypdf 文本抽取显示 HITL pdf 的 P18「五、关键知识点总结」、P23-24「关键变化点」
与 graph07 pdf 的 P21 几乎没有文字 —— 疑为图片/表格。抽出图片落盘再看。
只用 Anaconda 自带 pypdf，不装任何包。
"""
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent / "pdf"
OUT = HERE / "images"
OUT.mkdir(exist_ok=True)

TARGETS = {
    "hitl.pdf": [18, 23, 24],
    "graph07.pdf": [21, 1, 4],
}

for fname, pages in TARGETS.items():
    reader = PdfReader(str(HERE / fname))
    for pno in pages:
        if pno > len(reader.pages):
            print(f"[skip] {fname} P{pno}: 超出页数 {len(reader.pages)}")
            continue
        page = reader.pages[pno - 1]
        imgs = list(page.images)
        txt_len = len((page.extract_text() or "").strip())
        print(f"[{fname} P{pno}] images={len(imgs)} text_chars={txt_len}")
        for i, im in enumerate(imgs):
            ext = Path(im.name).suffix or ".png"
            dest = OUT / f"{Path(fname).stem}_p{pno}_{i}{ext}"
            dest.write_bytes(im.data)
            print(f"    -> {dest.name} ({len(im.data)} bytes)")
