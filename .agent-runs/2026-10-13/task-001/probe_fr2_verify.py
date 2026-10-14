# -*- coding: utf-8 -*-
"""FR-2 独立核验：原始 .xls 逐列 vs artifacts/intake_probe 产物"""
import json
from pathlib import Path
import xlrd

BASE = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
ART = Path(r"H:\mm-agent\artifacts\intake_probe")
FILES = {
    "附件1": "附件1 各门店客户满意度综合得分-A题.xls",
    "附件2": "附件2 各门店服务态度得分-A题.xls",
    "附件3": "附件3 各门店车辆清洁得分-A题.xls",
    "附件4": "附件4 各门店客户投诉量-A题.xls",
    "附件5": "附件5 公司发现各门店问题数量-A题.xls",
    "附件6": "附件6 各门店月利润定额及实际利润值-A题.xls",
}

rep = json.loads((ART / "schema_report.json").read_text(encoding="utf-8"))
prod = {}
for t in rep["tables"]:
    prod[t["source_file"]] = t
prod_fields = {}
for f in rep["fields"]:
    prod_fields.setdefault(f["source_file"], []).append(f)

for key, fname in FILES.items():
    path = BASE / fname
    book = xlrd.open_workbook(str(path), formatting_info=True)
    sh = book.sheet_by_index(0)
    print("=" * 100)
    print(f"### {key}  {fname}")
    print(f"raw: nrows={sh.nrows} ncols={sh.ncols}")
    print(f"merged_cells({len(sh.merged_cells)}): {sh.merged_cells}")
    for r in range(min(5, sh.nrows)):
        vals = [f"{i}:{sh.cell_value(r, i)!r}" for i in range(sh.ncols)]
        print(f"  row{r}: " + " | ".join(vals))
    # 每列非空计数
    print("  --- 列非空统计（col: 首值/非空数/尾值） ---")
    for c in range(sh.ncols):
        col = [sh.cell(r, c).value for r in range(sh.nrows)]
        non_empty = [v for v in col if v not in (None, "")]
        head = non_empty[0] if non_empty else None
        tail = non_empty[-1] if non_empty else None
        print(f"    col{c:>3}: {head!r} / n={len(non_empty)} / last={tail!r}")
    t = prod.get(fname, {})
    print(f"  --- 产物 table: rc={t.get('row_count')} fc={t.get('field_count')} title={t.get('table_title','')!r}")
    for f in prod_fields.get(fname, []):
        print(f"    field {f['field_raw']!r}: dtype={f['dtype']} rc={f['row_count']} miss={f['missing_rate']} samples={f['sample_values']}")
