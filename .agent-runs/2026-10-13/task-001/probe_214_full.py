# -*- coding: utf-8 -*-
"""214 穷举全量清单（只读）：把 总表.xlsx / 预处理-有资格月度.xlsx /
预处理2-排名及其获奖名单.xlsx 与 6 张附件的各 sheet 各口径行数全部打印，
并逐列标注是否命中 214 / 63。按门店 id 索引，禁止按行号。
"""
from __future__ import annotations
from pathlib import Path
import xlrd
from openpyxl import load_workbook

BASE = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
ATT = {
    "附件1": "附件1 各门店客户满意度综合得分-A题.xls",
    "附件2": "附件2 各门店服务态度得分-A题.xls",
    "附件3": "附件3 各门店车辆清洁得分-A题.xls",
    "附件4": "附件4 各门店客户投诉量-A题.xls",
    "附件5": "附件5 公司发现各门店问题数量-A题.xls",
    "附件6": "附件6 各门店月利润定额及实际利润值-A题.xls",
}


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def hdr(s):
    print("\n" + "=" * 110)
    print(s)
    print("=" * 110)


# ---- 6 附件 ----
hdr("A. 6 张附件（.xls）各口径行数")
print(f"{'文件':<8} {'物理行':>6} {'砍2行头':>7} {'首列非空':>8} {'首列去重':>8} {'任值行':>7} {'列数':>5}")
for tag, fname in ATT.items():
    sh = xlrd.open_workbook(str(BASE / fname), formatting_info=True).sheet_by_index(0)
    first = [str(sh.cell_value(r, 0)).strip() for r in range(2, sh.nrows)]
    nonblank = [v for v in first if v]
    anyv = sum(1 for r in range(2, sh.nrows)
               if any(sh.cell_value(r, c) not in (None, "") for c in range(sh.ncols)))
    print(f"{tag:<8} {sh.nrows:>6} {sh.nrows-2:>7} {len(nonblank):>8} {len(set(nonblank)):>8} {anyv:>7} {sh.ncols:>5}")

print("\n6 附件组合口径：")
tb = sum(xlrd.open_workbook(str(BASE/ATT[k]), formatting_info=True).sheet_by_index(0).nrows for k in ATT)
nb = sum(len([1 for r in range(2, xlrd.open_workbook(str(BASE/ATT[k]), formatting_info=True).sheet_by_index(0).nrows)
              if str(xlrd.open_workbook(str(BASE/ATT[k]), formatting_info=True).sheet_by_index(0).cell_value(r,0)).strip()])
         for k in ATT)
print(f"  物理行之和={tb}  首列非空行之和={nb}  12月×157门店=1884")

# ---- xlsx ----
hdr("B. xlsx 文件各 sheet 各口径行数")
for fname in ["总表.xlsx", "预处理-有资格月度.xlsx", "预处理2-排名及其获奖名单.xlsx"]:
    wb = load_workbook(str(BASE / fname), data_only=True, read_only=False)
    print(f"\n--- {fname}  sheets={wb.sheetnames}")
    print(f"  {'sheet':<24} {'max_row':>8} {'max_col':>7} {'非空行':>7} {'首列非空':>8} {'首列去重':>8}")
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        rows_any = [r for r in grid if any(v not in (None, "") for v in r)]
        first = [str(r[0]).strip() for r in rows_any if r and r[0] not in (None, "")]
        print(f"  {ws.title:<24} {ws.max_row:>8} {ws.max_column:>7} {len(rows_any):>7} {len(first):>8} {len(set(first)):>8}")
    wb.close()

# ---- 命中判定 ----
hdr("C. 命中 214 / 63 的 (文件·口径) 组合")
hits214, hits63 = [], []
for tag, fname in ATT.items():
    sh = xlrd.open_workbook(str(BASE / fname), formatting_info=True).sheet_by_index(0)
    first = [str(sh.cell_value(r, 0)).strip() for r in range(2, sh.nrows)]
    nonblank = [v for v in first if v]
    anyv = sum(1 for r in range(2, sh.nrows)
               if any(sh.cell_value(r, c) not in (None, "") for c in range(sh.ncols)))
    cand = {"物理行": sh.nrows, "砍2行头": sh.nrows-2, "首列非空": len(nonblank),
            "首列去重": len(set(nonblank)), "任值行": anyv,
            "行×列": sh.nrows*sh.ncols, "数值格": sum(1 for r in range(2, sh.nrows) for c in range(sh.ncols) if num(sh.cell_value(r,c)) is not None)}
    for k, v in cand.items():
        (hits214 if v == 214 else hits63 if v == 63 else []).append((tag, k, v))
for fname in ["总表.xlsx", "预处理-有资格月度.xlsx", "预处理2-排名及其获奖名单.xlsx"]:
    wb = load_workbook(str(BASE / fname), data_only=True, read_only=False)
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        rows_any = [r for r in grid if any(v not in (None, "") for v in r)]
        first = [str(r[0]).strip() for r in rows_any if r and r[0] not in (None, "")]
        cand = {"max_row": ws.max_row, "max_row-1": ws.max_row-1, "非空行": len(rows_any),
                "非空行-1": len(rows_any)-1, "首列非空": len(first), "首列去重": len(set(first)),
                "max_row×max_col": ws.max_row*ws.max_column}
        for k, v in cand.items():
            (hits214 if v == 214 else hits63 if v == 63 else []).append((f"{fname}::{ws.title}", k, v))
    wb.close()
print(f"命中 214 的组合：{len(hits214)} 个  {hits214}")
print(f"命中  63 的组合：{len(hits63)} 个  {hits63}")

# ---- 特判：157 + ? = 214 ----
hdr("D. 214 的构成推演")
print(f"  214 - 157(门店) = {214-157}")
print(f"  214 - 1(表头)   = {213}  214 / 157 = {214/157:.4f}  214 / 12 = {214/12:.4f}  214 / 7(文件) = {214/7:.4f}")
print(f"  157 门店 × 12 月 = {157*12};  157 门店 + 12 月列头 = {157+12};  12 月 + 157 门店 + 45? = ?")
print(f"  附件6 合并区 14 处；附件2/3 24 列；附件1 13 字段；附件6 37 逻辑字段")
