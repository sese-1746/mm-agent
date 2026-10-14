# -*- coding: utf-8 -*-
"""附件6 重复列模式核验（只读）：验证「定额列整列重复」的准确清单。

按门店 id 索引，逐月取 定额 col(3m-2) / 实际利润 col(3m-1) / 评级 col(3m)，
两两比较 157 个门店的值序列是否逐行相同。
"""
from __future__ import annotations
from pathlib import Path
import xlrd

BASE = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
ATT6 = "附件6 各门店月利润定额及实际利润值-A题.xls"

sh = xlrd.open_workbook(str(BASE / ATT6), formatting_info=True).sheet_by_index(0)

# 逐门店（skip 子表头行 r=2）
rows = []
for r in range(2, sh.nrows):
    sid = str(sh.cell_value(r, 0)).strip()
    if sid == "":
        continue
    rec = {"store": sid, "excel_row": r + 1}
    for m in range(1, 13):
        rec[("quota", m)] = sh.cell_value(r, 3 * m - 2)
        rec[("actual", m)] = sh.cell_value(r, 3 * m - 1)
        rec[("rate", m)] = str(sh.cell_value(r, 3 * m)).strip()
    rows.append(rec)

print(f"门店数 = {len(rows)}")

types = {"quota": "定额", "actual": "实际利润", "rate": "评级"}


def series(t, m):
    return tuple(r[(t, m)] for r in rows)


for t in ("quota", "actual", "rate"):
    # 找逐行完全相同的列组
    groups = {}
    for m in range(1, 13):
        key = series(t, m)
        groups.setdefault(key, []).append(m)
    dups = {tuple(v): k for k, v in groups.items() if len(v) > 1}
    print(f"\n[{types[t]}] 逐行完全相同的列组（157 门店全等）：")
    if not dups:
        print("    无重复列组")
    for months, key in sorted(dups.items()):
        print(f"    {months}  → 列 {[3*m-2+ (0 if t=='quota' else 1) for m in months]}")

# 重点给出队长清单里那几组的证据（取 2~3 行展示）
print("\n" + "=" * 90)
print("定额列重复组 —— 逐行证据（任取 3 行展示）")
print("=" * 90)
evidence_groups = [(1, 2), (3, 4, 11), (5, 9), (7, 8)]
picks = [0, 1, len(rows) // 2]  # 第 1、2、中位 行
for grp in evidence_groups:
    print(f"\n组 {grp}（1-based 月份）  原始列={[3*m-3 for m in grp]}（0-based clo=3m-3）")
    print(f"    {'门店':>6} {'Excel行':>7} " + " ".join(f"{'月'+str(m)+'定额':>10}" for m in grp))
    for p in picks:
        r = rows[p]
        vals = " ".join(f"{r[('quota', m)]:>10}" for m in grp)
        print(f"    {r['store']:>6} {r['excel_row']:>7} {vals}")

# 顺带核验：这些重复组的「实际利润」列是否也重复？
print("\n" + "=" * 90)
print("同一批月份的『实际利润』列是否也重复？")
print("=" * 90)
for grp in evidence_groups:
    for m in grp[1:]:
        same = series("actual", grp[0]) == series("actual", m)
        print(f"    实际利润 月{grp[0]} vs 月{m}: {'逐行相同' if same else '不同'}")
for grp in evidence_groups:
    for m in grp[1:]:
        same = series("rate", grp[0]) == series("rate", m)
        print(f"    评级     月{grp[0]} vs 月{m}: {'逐行相同' if same else '不同'}")

# 给出定额列有差异的行数，佐证「整列重复」是真实而非抽样巧合
print("\n" + "=" * 90)
print("全量列间差异计数（不同门店数，157 全等=0）")
print("=" * 90)
print(f"    {'对比':>16} {'定额差异数':>10} {'实际利润差异数':>14}")
base = [1, 2, 3, 4, 5, 7]
for a in base:
    for b in range(a + 1, 13):
        dq = sum(1 for r in rows if r[("quota", a)] != r[("quota", b)])
        da = sum(1 for r in rows if r[("actual", a)] != r[("actual", b)])
        if dq == 0 or da == 0:
            print(f"    月{a:>2} vs 月{b:>2} {dq:>12} {da:>14}")
