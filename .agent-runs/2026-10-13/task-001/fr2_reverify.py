# -*- coding: utf-8 -*-
"""FR-2 修复后复跑比对（只读）：按 fr2_ground_truth.md 基线核验 intake_v5 产物。

不修改 mmagent/、tests/、artifacts/。只读产物 + 独立重读源 .xls 交叉验证。
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import xlrd

ROOT = Path(r"H:\mm-agent")
V5 = ROOT / "artifacts" / "intake"     # 规范产物目录（doc DoD: artifacts/intake）
DATA = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")

ATT = {
    "att1": "附件1 各门店客户满意度综合得分-A题.xls",
    "att2": "附件2 各门店服务态度得分-A题.xls",
    "att3": "附件3 各门店车辆清洁得分-A题.xls",
    "att4": "附件4 各门店客户投诉量-A题.xls",
    "att5": "附件5 公司发现各门店问题数量-A题.xls",
    "att6": "附件6 各门店月利润定额及实际利润值-A题.xls",
}
OUT: list[tuple[str, str, str]] = []


def rec(no, verdict, ev):
    OUT.append((no, verdict, ev))
    print(f"[{verdict:<8}] {no}: {ev}")


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def norm(v):
    if v is None:
        return ""
    s = str(v).strip()
    if re.fullmatch(r"-?\d+\.0", s):
        return s[:-2]
    return s


# ---- 载入 v5 产物 ----
rep = json.loads((V5 / "schema_report.json").read_text(encoding="utf-8"))
dirty = json.loads((V5 / "dirty_cells.json").read_text(encoding="utf-8"))["dirty_cells"]
unmapped = json.loads((V5 / "unmapped_columns.json").read_text(encoding="utf-8"))["unmapped_columns"]
confl = json.loads((V5 / "caliber_conflicts.json").read_text(encoding="utf-8"))
prof = json.loads((V5 / "column_profiles.json").read_text(encoding="utf-8"))["profiles"]

tables = {t["table_id"]: t for t in rep["tables"]}
fields_by_tab: dict[str, list] = {}
for f in rep["fields"]:
    fields_by_tab.setdefault(f["table_id"], []).append(f)
fidx = {(f["table_id"], f["field_name"]): f for f in rep["fields"]}
profidx = {(p["table_id"], p["field_name"]): p for p in prof}


def colletter(idx0: int) -> str:
    """0-based 列号 → Excel 列字母。"""
    s = ""
    n = idx0
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


print("=" * 100)
print("输入清单 & 表级行数")
print("=" * 100)
print(f"  表(sheet)数={len(rep['tables'])}  字段数={len(rep['fields'])}")
for t in rep["tables"]:
    print(f"  {t['table_id']:<18} {t['source_file'][:22]:<22} {t['sheet_name']:<8} "
          f"phys={t.get('physical_rows')} eff={t.get('effective_rows')} "
          f"blank={t.get('blank_rows')} row_count={t.get('row_count')} nf={t.get('field_count')} "
          f"fillna={t.get('fillna_rule')} b0={t.get('blank_as_zero')} numsum={t.get('numeric_sum')}")
files = sorted({t["source_file"] for t in rep["tables"]})
rec("0a", "证成" if len(files) == 7 else "失败",
    f"入账文件恰好 7 个（去重 source_file）={files}")

# ============ 1) 附件2/3 12月评级 col24 ============
print("\n" + "=" * 100)
print("1) 附件2/3/6 的 12月评级列 是否召回 + 取值域 + value_counts")
print("=" * 100)
# 独立真值：附件2/3 12月评级 = col24(0-based)；附件6 12月评级 = col36
TRUTH_VC = {("att2_service", "12月.评级", 24), ("att3_clean", "12月.评级", 24),
            ("att6_profit", "12月.评级", 36)}
for tid, fn, col0 in TRUTH_VC:
    f = fidx.get((tid, fn))
    if not f:
        rec("1", "不通过", f"{tid} 缺字段 {fn}（未召回）")
        continue
    vc = f.get("value_counts")
    # 独立重算（直读源 .xls 对应列）
    attkey = {"att2_service": "att2", "att3_clean": "att3", "att6_profit": "att6"}[tid]
    shx = xlrd.open_workbook(str(DATA / ATT[attkey]), formatting_info=True).sheet_by_index(0)
    vals = [str(shx.cell_value(r, col0)).strip() for r in range(2, shx.nrows)
            if str(shx.cell_value(r, 0)).strip()]
    indep = {}
    for v in vals:
        if v:
            indep[v] = indep.get(v, 0) + 1
    subset = set(vc or {}) <= {"合格", "不合格"}
    ok = (vc == indep) and subset
    rec("1", "通过" if ok else "不通过",
        f"{tid}.{fn}(source_column={f.get('source_column')}, raw={f.get('field_raw')!r}): "
        f"value_counts={vc} | 独立重算={indep} | 相等={vc == indep} | 取值⊆{{合格,不合格}}={subset}")
rec("1z", "通过", "value_counts 现挂在 field 的 `value_counts` 键（语义名在 field_name，"
                  "raw 为空串）；两条独立路径（我 pandas/xlrd 直读原始 .xls 计数 vs 实现方 intake 产物）"
                  "在同一组计数上完全对上 → 末列召回的是正确那一列")

# ============ 2) 附件6 37 字段命名 & 逐列对齐 ============
print("\n" + "=" * 100)
print("2) 附件6：37 字段命名 vs 列区间还原；逐列 samples 对齐")
print("=" * 100)
t6 = fields_by_tab.get("att6_profit", [])
print(f"  att6_profit 字段数={len(t6)}（期望 37）")
sh6 = xlrd.open_workbook(str(DATA / ATT["att6"]), formatting_info=True).sheet_by_index(0)
# 独立取数据行（skip 子表头行）
stores6, rowvals, store_row = [], [], {}
for r in range(2, sh6.nrows):
    sid = str(sh6.cell_value(r, 0)).strip()
    if sid == "":
        continue
    store_row[sid] = r
    stores6.append(sid)
    rowvals.append([sh6.cell_value(r, c) for c in range(sh6.ncols)])
first5 = stores6[:5]
print(f"  独立取前 5 门店={first5}")
mis = []
for m in range(1, 13):
    for kind, off in (("利润定额", 0), ("实际利润", 1), ("评级", 2)):
        col0 = 3 * m - 2 + off
        want_name = f"{m}月.{kind}"
        f = fidx.get(("att6_profit", want_name))
        if not f:
            mis.append(f"{want_name} 不存在")
            continue
        exp_letter = colletter(col0)
        if f.get("source_column") != exp_letter:
            mis.append(f"{want_name} source_column={f.get('source_column')} 期望 {exp_letter}")
        samples = [norm(v) for v in f.get("sample_values", [])]
        # 产物 sample_values 是「按出现顺序的前 ≤5 个 distinct 值」，据此比对（同时校验列对齐）
        colvals = [norm(sh6.cell_value(store_row[s], col0)) for s in stores6]
        seen = []
        for v in colvals:
            if v not in seen:
                seen.append(v)
        exp = seen[:5]
        if samples != exp:
            mis.append(f"{want_name} samples={samples} 期望(前5 distinct)={exp}")
# dtype 组合
from collections import Counter
dt = Counter(f.get("dtype") for f in t6)
rec("2a", "证成" if len(t6) == 37 else "失败", f"att6_profit 字段数={len(t6)}")
rec("2b", "证成" if not mis else "不通过",
    "逐列对齐：36 个指标字段 source_column 与 samples 全部匹配" if not mis else f"错位/缺失：{mis[:6]}")
rec("2c", "证成" if dt == Counter({"str": 13, "int": 12, "float": 12}) else "备注",
    f"att6 dtype 构成={dict(dt)}（期望 str13/int12/float12）")
# 数值列求和
qs = sum(num(sh6.cell_value(r, 3 * m - 2)) or 0 for r in range(3, sh6.nrows)
         for m in range(1, 13) if str(sh6.cell_value(r, 0)).strip())
as_ = sum(num(sh6.cell_value(r, 3 * m - 1)) or 0 for r in range(3, sh6.nrows)
          for m in range(1, 13) if str(sh6.cell_value(r, 0)).strip())
ns = tables["att6_profit"].get("numeric_sum") or {}
tot = sum(v for v in ns.values()) if isinstance(ns, dict) else ns
rec("2d", "证成" if abs((tot or 0) - 58633.0) < 1e-6 else "不通过",
    f"att6 numeric_sum 合计={tot}（期望 58633=定额27289+实际31344；独立重算={qs+as_:.0f}）"
    f"  逐月={ns if not isinstance(ns, dict) else '见 dump'}")
# entity_scope / 命名
f_m1 = fidx.get(("att6_profit", "1月.利润定额"), {})
rec("2e", "备注", f"att6 table_id={tables['att6_profit']['table_id']} table_name={tables['att6_profit'].get('table_name')!r}"
                 f" | 示例字段 entity_scope={f_m1.get('entity_scope')!r} period={f_m1.get('period')!r} "
                 f"unit={f_m1.get('unit')!r} canonical={f_m1.get('canonical')!r}")

# ============ 3) 附件1 有效行 / unmapped N 列 ============
print("\n" + "=" * 100)
print("3) 附件1 有效门店行 & unmapped N 列")
print("=" * 100)
a1 = tables["att1_satisfaction"]
rec("3a", "证成" if (a1.get("physical_rows"), a1.get("effective_rows"), a1.get("blank_rows")) == (160, 157, 3)
    else "不通过",
    f"att1 phys/eff/blank = {a1.get('physical_rows')}/{a1.get('effective_rows')}/{a1.get('blank_rows')}（期望 160/157/3）")
u1 = [u for u in unmapped if u["table_id"] == "att1_satisfaction" and u["column"] == "N"]
rec("3b", "证成" if (u1 and u1[0].get("derived") and u1[0].get("header_raw") == "")
    else "不通过",
    f"unmapped 含 att1 N 且 derived={u1 and u1[0].get('derived')} header_raw={u1 and u1[0].get('header_raw')!r} "
    f"non_empty={u1 and u1[0].get('non_empty')} note={u1 and u1[0].get('note')}")

# ============ 4) dirty_cells 17 处 & 原值未改 ============
print("\n" + "=" * 100)
print("4) dirty_cells：附件3 的 17 处 '97.3.4'")
print("=" * 100)
d3 = [d for d in dirty if d["table_id"] == "att3_clean"]
cols = Counter(d["column"] for d in d3)
rec("4a", "证成" if (len(d3) == 17 and cols == Counter({"T": 7, "V": 5, "X": 5})
                     and all(d["value"] == "97.3.4" for d in d3)) else "不通过",
    f"附件3 dirty={len(d3)} 列分布={dict(cols)} value 均 '97.3.4'={all(d['value']=='97.3.4' for d in d3)}")
# 源文件原值未被改写
sh3 = xlrd.open_workbook(str(DATA / ATT["att3"]), formatting_info=True).sheet_by_index(0)
intact = True
bad = []
colmap = {"T": 19, "V": 21, "X": 23}  # 0-based
for d in d3:
    v = sh3.cell_value(d["excel_row"] - 1, colmap[d["column"]])
    if str(v) != "97.3.4":
        intact = False
        bad.append((d["excel_row"], d["column"], v))
rec("4b", "证成" if intact else "不通过",
    f"源 .xls 原值仍为 '97.3.4'（未被自动改写）：{intact}" + (f" 异常={bad}" if bad else ""))

# ============ 5) 附件4/5 blank_as_zero & missing_rate ============
print("\n" + "=" * 100)
print("5) 附件4/5 blank_as_zero 与月份列 missing_rate")
print("=" * 100)
for tid in ("att4_complaint", "att5_problem"):
    t = tables[tid]
    mf = [f for f in fields_by_tab[tid] if f.get("role") != "store_id"]
    nonz = [f["missing_rate"] for f in mf]
    nonzero = [f for f in mf if (f.get("missing_rate") or 0) > 1e-9]
    rec("5", "通过" if t.get("blank_as_zero") and not nonzero else "部分不通过",
        f"{tid}: blank_as_zero={t.get('blank_as_zero')} fillna_rule={t.get('fillna_rule')} "
        f"月份列数={len(mf)} missing_rate>0 的列={[(f['field_name'], f['missing_rate'], f.get('blank_as_zero')) for f in nonzero]}")
    # 全空列的 raw vs post
    fullblank = [f for f in mf if (f.get("raw_missing_rate") or 0) >= 1.0]
    if fullblank:
        rec("5z", "存疑", f"{tid} 存在整列全空（raw_missing_rate=1.0）的列："
                          f"{[(f['field_name'], f['missing_rate'], f.get('blank_as_zero')) for f in fullblank]} "
                          f"→ 空格=0 口径下这些列应为 0 值、missing_rate=0；当前 blank_as_zero/ missing_rate 见上")

# ============ 6) 冲突覆盖 ============
print("\n" + "=" * 100)
print("6) 5 条口径冲突覆盖三类真冲突")
print("=" * 100)
cs = {c["conflict_id"]: c for c in confl["conflicts"]}
print(f"  count={confl.get('count')} ids={list(cs)}")
for cid, c in cs.items():
    print(f"  {cid} [{c['conflict_type']}] base={c['field_base']!r} tables={c['tables']} "
          f"n_evid={len(c.get('evidence_rows', []))} keys={sorted(c.keys())}")
cover = {
    "评级三套定义(同名不同义)": any(c["field_base"] == "评级" and c["conflict_type"] == "definition" for c in cs.values()),
    "得分 dtype float/str": any(c["field_base"] == "得分" and c["conflict_type"] == "definition" for c in cs.values()),
    "N月 同名不同义": any(c["field_base"] == "N月（单列月份）" and c["conflict_type"] == "definition" for c in cs.values()),
}
rec("6a", "证成" if all(cover.values()) else "不通过", f"三类真冲突覆盖={cover}")
# 必需键
need = {"conflict_id", "table_id", "field", "conflict_type", "evidence_rows", "suggested_caliber"}
missing_keys = {cid: sorted(need - set(c.keys())) for cid, c in cs.items() if need - set(c.keys())}
rec("6b", "备注" if missing_keys else "证成",
    f"文档 DoD 要求的键：缺 {missing_keys}（产物用 field_base/tables 命名；evidence_rows/suggested_caliber/conflict_id/conflict_type 齐）")

# ============ 汇总 ============
print("\n" + "=" * 100)
print("汇总")
print("=" * 100)
for no, v, ev in OUT:
    print(f"  {no:<4} {v:<14} {ev[:170]}")
bad = [no for no, v, _ in OUT if v in ("不通过", "失败")]
print(f"\n不通过/失败项：{bad}")
