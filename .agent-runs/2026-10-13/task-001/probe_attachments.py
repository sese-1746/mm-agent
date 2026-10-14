# -*- coding: utf-8 -*-
"""只读探测 2024 数模校赛 A 题附件1~6 的 .xls 结构"""
import os, glob, sys
import xlrd

BASE = r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题"

print("=== 目录全部文件清单 ===")
for f in sorted(os.listdir(BASE)):
    full = os.path.join(BASE, f)
    size = os.path.getsize(full) if os.path.isfile(full) else -1
    print(f"  {f}  ({size} bytes)")

files = sorted(glob.glob(os.path.join(BASE, "附件[1-6]*.xls")))
print(f"\n=== 命中附件 .xls 数量: {len(files)} ===")

for path in files:
    name = os.path.basename(path)
    print("\n" + "=" * 70)
    print(f"文件: {name}")
    try:
        book = xlrd.open_workbook(path, formatting_info=True)
        print(f"  xlrd 读取: OK (xlrd {xlrd.__version__})")
    except Exception as e:
        print(f"  xlrd 读取失败: {type(e).__name__}: {e}")
        try:
            book = xlrd.open_workbook(path)  # 不带 formatting_info 重试
            print("  不带 formatting_info 重试: OK")
        except Exception as e2:
            print(f"  重试仍失败: {type(e2).__name__}: {e2}")
            continue
    print(f"  sheet 列表: {book.sheet_names()}")
    for sh in book.sheets():
        print(f"\n  [sheet] {sh.name!r}: {sh.nrows} 行 x {sh.ncols} 列")
        # 合并单元格
        try:
            merged = sh.merged_cells
            print(f"  合并单元格数量: {len(merged)}", end="")
            if merged:
                print(f"  示例(前5): {merged[:5]}")
            else:
                print()
        except Exception as e:
            print(f"  合并单元格信息不可用: {e}")
        # 表头(第1行)与前3行内容
        for r in range(min(3, sh.nrows)):
            row = sh.row_values(r)
            row = [("" if v == "" else v) for v in row]
            print(f"  第{r+1}行: {row}")
        # 缺失值统计
        total = sh.nrows * sh.ncols
        empty = 0
        for r in range(sh.nrows):
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    empty += 1
        print(f"  单元格总数: {total}, 空/空白单元格: {empty} ({100.0*empty/total if total else 0:.1f}%)")

# 额外：pandas 读法验证
print("\n" + "=" * 70)
print("=== pandas.read_excel 验证 ===")
import pandas as pd
for path in files:
    name = os.path.basename(path)
    try:
        df = pd.read_excel(path, engine="xlrd")
        print(f"{name}: shape={df.shape}, columns={list(df.columns)[:8]}")
    except Exception as e:
        print(f"{name}: pandas 读取失败: {type(e).__name__}: {e}")
