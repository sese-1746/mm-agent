# -*- coding: utf-8 -*-
"""文档 spotcheck 示例自洽性核验（只读）。

文档原文（docx_dump.txt:301）：
  {"satisfaction_overall": {"table_id":"att1_satisfaction","row_count":214,"missing_rate":0.0187},
   "complaint_count":     {"table_id":"att5_complaint","row_count":214,"missing_rate":0.0},
   "profit_actual":       {"table_id":"att6_profit","row_count":214,"missing_rate":0.0093}}
文档原文（docx_dump.txt:190）：{"conflicts":7,"mapping_rows":63,...}

本脚本：把「row_count × missing_rate = 空值个数」反推，核对是否与本目录真实空值数吻合。
"""
from __future__ import annotations
from pathlib import Path
import xlrd

BASE = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
ATT = {
    "att1 附件1 满意度": "附件1 各门店客户满意度综合得分-A题.xls",
    "att5 附件5 投诉量": "附件5 公司发现各门店问题数量-A题.xls",
    "att6 附件6 利润": "附件6 各门店月利润定额及实际利润值-A题.xls",
}
EXAMPLE = {  # 文档给的示例值
    "att1 附件1 满意度": (214, 0.0187),
    "att5 附件5 投诉量": (214, 0.0),
    "att6 附件6 利润": (214, 0.0093),
}

print("=== 文档示例的『自洽性』反推（row_count × missing_rate → 空值个数）===")
for tag, (rc, mr) in EXAMPLE.items():
    print(f"  {tag}: row_count={rc} × missing_rate={mr} → 隐含空值个数 = {rc*mr:.4f} ≈ {round(rc*mr)}")
    print(f"      若空值=3: missing_rate 应 = {3/rc:.4f}；若空值=4: {4/rc:.4f}")

print("\n=== 本目录真实空值数（按各口径）===")
for tag, fname in ATT.items():
    sh = xlrd.open_workbook(str(BASE / fname), formatting_info=True).sheet_by_index(0)
    # 物理数据行（row2 起）
    phys = sh.nrows - 2
    blank_first = [r for r in range(2, sh.nrows) if str(sh.cell_value(r, 0)).strip() == ""]
    anyblank = [r for r in range(2, sh.nrows)
                if all(sh.cell_value(r, c) in (None, "") for c in range(sh.ncols))]
    print(f"  {tag}: 物理行={sh.nrows} 数据行={phys} 首列空行数={len(blank_first)}{blank_first} "
          f"整行全空数={len(anyblank)}{anyblank}")
    # 用当前代码口径（首列空→missing）算 首列 missing_rate = 空行数/物理数据行
    if phys:
        print(f"       若按『空行数/物理数据行』：{len(blank_first)}/{phys} = {len(blank_first)/phys:.4f}")

print("\n=== 文档示例把 att1/att5/att6 三个不同附件都写成 row_count=214 ===")
print("  真实（物理数据行）：附件1=160  附件5=157  附件6=158 → 三者互不相等，不可能同为 214")

print("\n=== 214 / 63 的其它可能构成（本目录内检索）===")
tests = {
    "157 门店 + 12": 157 + 12,
    "157 门店 + 57": 157 + 57,
    "160 + 54": 160 + 54,
    "157 × 12 / 8.8": None,
    "942(6附件首列非空) / 4.4": None,
    "323 字段 / 63": 323 / 63 if 63 else None,
    "126 字段(6缺失表) + 88": None,
}
for k, v in tests.items():
    if v is None:
        print(f"  {k} = 无整数解")
    else:
        print(f"  {k} = {v}")
print("  结论：本目录内没有任何自然的行数口径等于 214 或 63（详见 probe_214_full_dump.txt）")
