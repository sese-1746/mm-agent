# -*- coding: utf-8 -*-
"""FR-2 独立真值参考（只读）：逐门店按 id 索引计算，禁止按行号对齐。

覆盖：
1) 附件1 每门店 12 月满意度均值（与 N 列年度均值互校）+ A1/D7/B14 + 有效门店行数
2) 附件2/3 的 12 月评级列（Y/col24）取值分布
3) 附件4/5 每门店年度合计（空格按 0）
4) 附件6 每门店全年「实际利润」「利润定额」合计 + 12 个月的列区间（merged_cells 还原）+ raw row2 子表头三元组
5) 214 穷举：全目录各文件各 sheet 的多种行数口径
"""
from __future__ import annotations

import json
from pathlib import Path

import xlrd
from openpyxl import load_workbook

BASE = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
ATT = {
    "att1": "附件1 各门店客户满意度综合得分-A题.xls",
    "att2": "附件2 各门店服务态度得分-A题.xls",
    "att3": "附件3 各门店车辆清洁得分-A题.xls",
    "att4": "附件4 各门店客户投诉量-A题.xls",
    "att5": "附件5 公司发现各门店问题数量-A题.xls",
    "att6": "附件6 各门店月利润定额及实际利润值-A题.xls",
}


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def load_sheet(fname):
    return xlrd.open_workbook(str(BASE / fname), formatting_info=True).sheet_by_index(0)


# ============================ 1) 附件1 ============================
print("#" * 100)
print("# 1) 附件1：每门店 12 月满意度均值（独立算，与 N 列年度均值互校）")
sh = load_sheet(ATT["att1"])
rows = []
blank_rows = []
for r in range(2, sh.nrows):
    sid = str(sh.cell_value(r, 0)).strip()
    if sid == "":
        blank_rows.append(r)
        continue
    months = [num(sh.cell_value(r, c)) for c in range(1, 13)]
    col13 = num(sh.cell_value(r, 13))       # N 列：文件自带的年度均值
    calc = sum(m for m in months if m is not None)
    cnt = sum(1 for m in months if m is not None)
    rows.append({"store": sid, "excel_row": r + 1, "n_months": cnt,
                 "calc_mean": calc / cnt, "file_col13": col13})
print(f"  物理数据行 = {sh.nrows - 2}（row2..row{sh.nrows-1}，0-based）")
print(f"  全空分区隔行（0-based row） = {blank_rows}  → Excel 行号 {[r+1 for r in blank_rows]}")
print(f"  有效门店行数 = {len(rows)}（期望 157）")
print(f"  门店 id 唯一 = {len({r['store'] for r in rows})}")
print(f"  门店顺序前 12 = {[r['store'] for r in rows[:12]]}")
maxdiff = max(abs(r["calc_mean"] - r["file_col13"]) for r in rows if r["file_col13"] is not None)
print(f"  独立均值 vs N 列年度均值 的最大绝对误差 = {maxdiff:.3e}  → {'一致（N 列=12月算术平均）' if maxdiff < 1e-9 else '不一致！'}")
print("  指定门店：")
for want in ("A1", "B14", "D7"):
    rec = next(r for r in rows if r["store"] == want)
    print(f"    {want}: Excel行{rec['excel_row']} 月份数={rec['n_months']} "
          f"独立均值={rec['calc_mean']:.4f}  N列值={rec['file_col13']!r}  N列4位={rec['file_col13']:.4f}")
print("  前 5 / 末 5 门店均值（供比对）：")
for r in rows[:5] + rows[-5:]:
    print(f"    {r['store']}: {r['calc_mean']:.4f}")

# ============================ 2) 附件2/3 12 月评级 ============================
print("#" * 100)
print("# 2) 附件2 / 附件3：12 月评级列（Y / col24）取值分布")
for key in ("att2", "att3"):
    sh2 = load_sheet(ATT[key])
    vals = [str(sh2.cell_value(r, 24)).strip() for r in range(2, sh2.nrows)]
    ne = [v for v in vals if v != ""]
    dist = {v: ne.count(v) for v in sorted(set(ne))}
    print(f"  {key} ({ATT[key][:4]}) 行2..row{sh2.nrows-1}(0-based) 非空 {len(ne)} 个，分布={dist}")
    print(f"    门店-评级（前 5）: " + ", ".join(
        f"{sh2.cell_value(r,0)}={sh2.cell_value(r,24)}" for r in range(2, 7)))
    # 12 月得分列（col23）以免被误当评级
    sc = [num(sh2.cell_value(r, 23)) for r in range(2, sh2.nrows)]
    sc = [v for v in sc if v is not None]
    print(f"    12 月得分列 col23 非空 {len(sc)} 个，min={min(sc)} max={max(sc)}")

# ============================ 3) 附件4/5 年度合计 ============================
print("#" * 100)
print("# 3) 附件4 / 附件5：每门店年度合计（空格按 0 计）")
tot = {}
for key in ("att4", "att5"):
    sh3 = load_sheet(ATT[key])
    per = {}
    for r in range(2, sh3.nrows):
        sid = str(sh3.cell_value(r, 0)).strip()
        if sid == "":
            continue
        vals = [num(sh3.cell_value(r, c)) for c in range(1, 13)]
        per[sid] = sum(v for v in vals if v is not None)  # 空格=0
        per[sid + "__nonzero"] = sum(1 for v in vals if v is not None)
    tot[key] = per
    nz = {s: per[s + "__nonzero"] for s in per if not s.endswith("__nonzero")}
    print(f"  {key}: 门店数={len(nz)} 年度合计总和={sum(per[s] for s in nz)} "
          f"单店合计 min={min(per[s] for s in nz)} max={max(per[s] for s in nz)} "
          f"有数值的月份格总数={sum(nz.values())}")
    for want in (list(nz)[0], list(nz)[len(nz) // 2], list(nz)[-1]):
        print(f"    {want}: 年度合计={per[want]}")
    print(f"    逐门店合计（前 8）: {[(s, per[s]) for s in list(nz)[:8]]}")

# ============================ 4) 附件6 ============================
print("#" * 100)
print("# 4) 附件6：列区间还原（merged_cells）+ 每门店全年合计")
sh6 = load_sheet(ATT["att6"])
merges = sh6.merged_cells
print(f"  合并单元格 {len(merges)} 处: {merges}")
month_groups = []
for (rlo, rhi, clo, chi) in sorted(merges, key=lambda m: m[2]):
    if rlo == 1 and clo > 0:
        month_groups.append((sh6.cell_value(1, clo), clo, chi))
print("  月份合并区还原（row1；chi 为开区间，实际列 = clo..chi）：")
for label, clo, chi in month_groups:
    sub = [str(sh6.cell_value(2, c)) for c in range(clo, chi + 1)]
    rate_col = clo + 2
    print(f"    {label}月: 合并 cols{clo}..{chi}  子表头={sub}  "
          f"→ 区间=[定额 col{clo}, 实际利润 col{clo+1}, 评级 col{rate_col}] "
          f"评级子表头={sh6.cell_value(2, rate_col)!r}")
print(f"  raw row2 子表头三元组 = {[tuple(str(sh6.cell_value(2, c)) for c in (1, 2, 3))]}")
print(f"  raw row2 逐列文本 = {[str(sh6.cell_value(2, c)) for c in range(sh6.ncols)]}")
print(f"  月份行 row1 = {[sh6.cell_value(1, c) for c in range(sh6.ncols)]}")

per6 = {}
skipped = []
for r in range(2, sh6.nrows):
    sid = str(sh6.cell_value(r, 0)).strip()
    if sid == "":
        skipped.append((r, "子表头行" if r == 2 else "全空行"))
        continue
    quota = [num(sh6.cell_value(r, 3 * m - 2)) for m in range(1, 13)]
    actual = [num(sh6.cell_value(r, 3 * m - 1)) for m in range(1, 13)]
    rate = [str(sh6.cell_value(r, 3 * m)).strip() for m in range(1, 13)]
    per6[sid] = {
        "quota_sum": sum(v for v in quota if v is not None),
        "actual_sum": sum(v for v in actual if v is not None),
        "n_quota": sum(1 for v in quota if v is not None),
        "n_actual": sum(1 for v in actual if v is not None),
        "rate_ok": sum(1 for v in rate if v == "合格"),
        "rate_bad": sum(1 for v in rate if v == "不合格"),
        "rate_blank": sum(1 for v in rate if v == ""),
    }
print(f"  跳过行: {skipped}")
print(f"  门店数 = {len(per6)}（期望 157）")
print(f"  定额合计总和 = {sum(v['quota_sum'] for v in per6.values()):.4f}；"
      f"实际利润合计总和 = {sum(v['actual_sum'] for v in per6.values()):.4f}")
for want in ("A1", "D7", "B14"):
    v = per6[want]
    print(f"    {want}: 定额合计={v['quota_sum']:.4f}(n={v['n_quota']}) "
          f"实际利润合计={v['actual_sum']:.4f}(n={v['n_actual']}) "
          f"合格={v['rate_ok']} 不合格={v['rate_bad']} 空={v['rate_blank']}")

# ============================ 5) 214 穷举 ============================
print("#" * 100)
print("# 5) 穷举：全目录各文件/各 sheet 的行数口径，找 214（也顺带找 63）")
records = []


def add(tag, calibers: dict):
    for k, v in calibers.items():
        records.append((tag, k, v))


# 6 张附件
for key, fname in ATT.items():
    shx = load_sheet(fname)
    first_col = [str(shx.cell_value(r, 0)).strip() for r in range(2, shx.nrows)]
    nonblank_first = [v for v in first_col if v != ""]
    any_nonblank = [r for r in range(2, shx.nrows)
                    if any(shx.cell_value(r, c) not in (None, "") for c in range(shx.ncols))]
    numeric_cells = sum(1 for r in range(2, shx.nrows) for c in range(shx.ncols)
                        if num(shx.cell_value(r, c)) is not None)
    add(f"{key} {fname}", {
        "物理行(含标题表头)": shx.nrows,
        "砍2行表头后的行": shx.nrows - 2,
        "首列非空行(门店行)": len(nonblank_first),
        "首列非空去重(门店数)": len(set(nonblank_first)),
        "任一行有值的行": len(any_nonblank),
        "列数": shx.ncols,
        "数值单元格总数": numeric_cells,
        "行×列": shx.nrows * shx.ncols,
    })
    if key in ("att2", "att3"):
        add(f"{key} 专属", {"月份列数×门店数": (shx.ncols - 2) * len(set(nonblank_first))})

# .xlsx 文件
for fname in ["总表.xlsx", "预处理-有资格月度.xlsx", "预处理2-排名及其获奖名单.xlsx"]:
    wb = load_workbook(str(BASE / fname), data_only=True, read_only=False)
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        rows_with_any = [r for r in grid if any(v not in (None, "") for v in r)]
        first = [str(r[0]).strip() for r in rows_with_any if r and r[0] not in (None, "")]
        numeric_cells = sum(1 for r in grid for v in r
                            if isinstance(v, (int, float)) and not isinstance(v, bool))
        add(f"{fname} :: {ws.title}", {
            "ws.max_row(物理行)": ws.max_row,
            "ws.max_column": ws.max_column,
            "非空行(任一格有值)": len(rows_with_any),
            "非空行-1(当表头)": max(0, len(rows_with_any) - 1),
            "首列非空行": len(first),
            "首列非空去重": len(set(first)),
            "数值单元格总数": numeric_cells,
            "max_row×max_column": ws.max_row * ws.max_column,
        })
    wb.close()

print(f"  共采集 {len(records)} 个 (文件·口径) 组合。命中 214 / 63 的组合：")
for target in (214, 63):
    hits = [(t, k, v) for (t, k, v) in records if v == target]
    print(f"    == {target} → {len(hits)} 个组合:")
    for t, k, v in hits:
        print(f"        {t} | {k} = {v}")

# 组合口径：6 附件合计
att_only = [r for r in records if r[0].startswith(("att", "att6"))]
print("  额外组合候选（跨表求和）：")
sums = {
    "6附件 首列非空行之和": sum(v for (t, k, v) in records if t.startswith("att") and k == "首列非空行(门店行)"),
    "6附件 去重门店之和": sum(v for (t, k, v) in records if t.startswith("att") and k == "首列非空去重(门店数)"),
    "6附件 物理行之和": sum(v for (t, k, v) in records if t.startswith("att") and k == "物理行(含标题表头)"),
}
for k, v in sums.items():
    print(f"    {k} = {v}{'   ← 命中 214!' if v == 214 else ''}")

# 附件1 的 N 列 = 年均值（157 个），以及 12 月 × 门店 的乘积
print("  其它候选：")
sh1 = load_sheet(ATT["att1"])
stores1 = {str(sh1.cell_value(r, 0)).strip() for r in range(2, sh1.nrows) if str(sh1.cell_value(r, 0)).strip()}
print(f"    附件1 门店数={len(stores1)}；12月×门店={len(stores1)*12}；"
      f"+12(月度列头)={len(stores1)+12}；×12-12={len(stores1)*12-12}")
print(f"    (157+57=214 的 57 在本目录无来源；见报告结论)")
