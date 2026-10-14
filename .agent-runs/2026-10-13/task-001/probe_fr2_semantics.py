# -*- coding: utf-8 -*-
"""FR-2 补充核验（v2，修正列对齐判定）：用"取值签名"把产物字段反查到原始列"""
import csv, json
from pathlib import Path
import xlrd

BASE = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
ART = Path(r"H:\mm-agent\artifacts\intake_probe")
rep = json.loads((ART / "schema_report.json").read_text(encoding="utf-8"))
byfile = {}
for f in rep["fields"]:
    byfile.setdefault(f["source_file"], []).append(f)


def sig(values):
    """取前 5 个去重非空值的字符串签名。"""
    out = []
    for v in values:
        s = str(v)
        if v in (None, "") or s in out:
            continue
        out.append(s)
        if len(out) >= 5:
            break
    return tuple(out)


for fname in ["附件2 各门店服务态度得分-A题.xls", "附件3 各门店车辆清洁得分-A题.xls",
              "附件6 各门店月利润定额及实际利润值-A题.xls",
              "附件1 各门店客户满意度综合得分-A题.xls"]:
    sh = xlrd.open_workbook(str(BASE / fname), formatting_info=True).sheet_by_index(0)
    # 该 sheet 的数据起始行：产物里 row_count 已知，用"标题行数"反推：这里直接由人工给定
    data_start = {"附件1 各门店客户满意度综合得分-A题.xls": 2,
                  "附件2 各门店服务态度得分-A题.xls": 2,
                  "附件3 各门店车辆清洁得分-A题.xls": 2,
                  "附件6 各门店月利润定额及实际利润值-A题.xls": 2}[fname]
    print("=" * 100)
    print(f"### {fname}  nrows={sh.nrows} ncols={sh.ncols}")
    col_sig = {c: sig([sh.cell_value(r, c) for r in range(data_start, sh.nrows)])
               for c in range(sh.ncols)}
    print("  原始列签名（数据行，前5个去重非空值）:")
    for c in range(sh.ncols):
        if col_sig[c]:
            print(f"    col{c:>2}: {col_sig[c]}")
    print("  产物字段 → 取值签名精确匹配的原始列：")
    for fl in byfile[fname]:
        s = tuple(str(x) for x in fl["sample_values"])
        matches = [c for c, cs in col_sig.items() if cs == s]
        note = "唯一" if len(matches) == 1 else (f"多列同签名({matches})" if matches else "无匹配")
        print(f"    field={fl['field_raw']!r:<9} dtype={fl['dtype']:<6} miss={fl['missing_rate']:<7} "
              f"samples={list(s)[:3]} → 匹配原始列={matches} [{note}]")
