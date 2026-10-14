# -*- coding: utf-8 -*-
"""提取两份规划/计划书 docx 全文（段落 + 表格）"""
import sys
from docx import Document

PATHS = [
    r"C:\Users\Lenovo\Desktop\agent项目\场景一-数模竞赛协作Agent-实施规划书.docx",
    r"C:\Users\Lenovo\Desktop\agent项目\何昆阳_场景一-数模竞赛协作Agent-项目开发计划书.docx",
]

for p in PATHS:
    print("\n" + "#" * 90)
    print("# FILE:", p)
    print("#" * 90)
    try:
        doc = Document(p)
    except Exception as e:
        print("!! open failed:", type(e).__name__, e)
        continue

    print(f"--- 段落数 {len(doc.paragraphs)} / 表格数 {len(doc.tables)} ---\n")
    # 按 body 顺序遍历，保留结构
    from docx.oxml.ns import qn
    body = doc.element.body
    ti = 0
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            # find matching paragraph by text
            texts = child.findall('.//' + qn('w:t'))
            t = "".join(x.text or "" for x in texts).strip()
            if t:
                print(t)
        elif child.tag == qn('w:tbl'):
            if ti < len(doc.tables):
                tbl = doc.tables[ti]
                print(f"\n[TABLE {ti}] rows={len(tbl.rows)} cols={len(tbl.columns)}")
                for r in tbl.rows:
                    cells = [c.text.replace("\n", " / ").strip() for c in r.cells]
                    print(" | ".join(cells))
                print()
            ti += 1
