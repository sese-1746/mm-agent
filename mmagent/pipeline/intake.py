"""FR-2 附件体检（确定性部分）：扫描黄金输入，产出体检产物。

产物（写入 out_dir）：
- schema_report.json      每表每字段：语义名/原始名/dtype/三口径行数/缺失率/样例值
- caliber_conflicts.json  口径冲突（definition/unit/period/entity_scope），含 evidence_rows
- field_mapping.csv       字段映射：语义名 field_name + canonical + period/entity_scope/unit
                          + fillna_rule + dup_group，confirmed_by/at 留空待人工
- intake_summary.md       人读摘要（行数三口径、缺失率 Top10、冲突清单、口径来源、已知数据问题）
- unmapped_columns.json   未映射/派生列留痕（禁止静默丢弃）
- dirty_cells.json        文本混入数值列的脏单元格清单（只留痕，不改值）
- column_profiles.json    列画像（含逐行全等的重复列组 dup_group）

表头支持 1~3 行复合表头：标题行 / 月份行（含合并区）/ 子表头行（如 利润定额/实际利润）。
列分组优先使用 merged_cells 归属；无合并信息时按月份标签间距推断。
读取规则：.xls 用 xlrd，.xlsx 用 openpyxl；配置读 config/field_aliases.yaml 与 config/intake_scope.yaml。
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import xlrd
import yaml
from openpyxl import load_workbook

MAX_SAMPLES = 5
_UNIT_RE = re.compile(r"[（(\[【]\s*([^（）()\[\]【】]{1,12}?)\s*[）)\]】]")
_PERIOD_WORDS = ("年", "季度", "月", "周", "日", "日均", "月均", "年均")
_SCOPE_WORDS = ("全国", "全省", "全市", "全区", "全县", "城镇", "农村", "地区", "门店")
_MONTH_LABEL_RE = re.compile(r"^\s*(\d{1,2})(?:\.0+)?\s*$")
# 多段小数（如 '97.3.4'）——典型的文本混入数值列
_MULTIDOT_RE = re.compile(r"^\d+(?:\.\d+){2,}$")
_NON_UNIT_CHARS = set("为空格处见注说以")
_MAX_HEADER_SCAN = 6      # 表头探测最多看前 6 行
CONFLICT_KEYS = ("conflict_id", "conflict_type", "field_base", "variants",
                 "tables", "evidence_rows", "suggested_caliber")

REPO_ROOT = Path(__file__).resolve().parents[2]
ALIAS_PATH = REPO_ROOT / "config" / "field_aliases.yaml"
SCOPE_PATH = REPO_ROOT / "config" / "intake_scope.yaml"
# 被白名单排除的文件统一说明（写进 skipped_files 留痕）
SKIP_REASON = "不在 FR-2 黄金输入白名单内（config/intake_scope.yaml），属预处理参考材料"
# 低基数文本列（如 N月.评级、门店列）才记录 value_counts，供 golden 计数断言
VALUE_COUNTS_MAX_DISTINCT = 50


def _load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _load_aliases(path: Path = ALIAS_PATH) -> dict:
    """读取字段别名表（缺失时返回空表，不阻断体检）。"""
    return _load_yaml(path)


def _load_scope(path: Path = SCOPE_PATH) -> dict:
    """读取输入白名单（include / skip_keywords）。"""
    return _load_yaml(path)


def _norm_field(name: str) -> str:
    """字段规范化：去全部空白并小写。"""
    return re.sub(r"\s+", "", str(name)).lower()


def _cell(v) -> str:
    """单元格转字符串（None → 空串，去首尾空白）。"""
    return "" if v is None else str(v).strip()


def _looks_like_unit(text: str) -> bool:
    """判断括号内容像不像单位（如「万元」像、「空格处为0」不像）。"""
    text = (text or "").strip()
    if not text or len(text) > 4:
        return False
    return not (set(text) & _NON_UNIT_CHARS)


def _infer_dtype(values: list) -> str:
    """按非空值推断字段类型：int / float / str / datetime / empty。"""
    seen = set()
    for v in values:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            continue
        if isinstance(v, bool):
            seen.add("int")
        elif isinstance(v, int):
            seen.add("int")
        elif isinstance(v, float):
            seen.add("int" if v.is_integer() else "float")
        else:
            s = str(v).strip()
            if re.fullmatch(r"\d{4}[-/年]\d{1,2}([-/月]\d{1,2}日?)?", s):
                seen.add("datetime")
            else:
                seen.add("str")
    if not seen:
        return "empty"
    if seen <= {"int"}:
        return "int"
    if seen <= {"int", "float"}:
        return "float"
    if seen == {"datetime"}:
        return "datetime"
    return "str"


# ---------------------------------------------------------------- 读取层

def _read_xls(path: Path) -> list[dict]:
    """xlrd 读取 .xls → [{sheet_name, grid, merges(0-based 半开区间)}]。"""
    book = xlrd.open_workbook(str(path), formatting_info=True)
    sheets = []
    for sh in book.sheets():
        if sh.nrows == 0 or sh.ncols == 0:
            continue
        grid = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
        merges = [(rlo, rhi, clo, chi) for (rlo, rhi, clo, chi) in sh.merged_cells]
        sheets.append({"sheet_name": sh.name, "grid": grid, "merges": merges})
    return sheets


def _read_xlsx(path: Path) -> list[dict]:
    """openpyxl 读取 .xlsx（data_only），合并区归一为 0-based 半开区间。"""
    wb = load_workbook(str(path), read_only=False, data_only=True)
    sheets = []
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        if not grid or all(_cell(v) == "" for v in grid[0]):
            if not grid:
                continue
        merges = [(r.min_row - 1, r.max_row, r.min_col - 1, r.max_col)
                  for r in ws.merged_cells.ranges]
        sheets.append({"sheet_name": ws.title, "grid": grid, "merges": merges})
    wb.close()
    return sheets


# ---------------------------------------------------------------- 表头解析

def _find_header_row(grid: list[list]) -> int:
    """取前几行中第一个非空单元格 ≥2 的行作为表头行。"""
    for i, row in enumerate(grid[:_MAX_HEADER_SCAN]):
        if sum(1 for v in row if _cell(v) != "") >= 2:
            return i
    return 0


def _looks_like_sub_header(row: list, hdr_idx: int, grid: list[list]) -> bool:
    """判断 header 行的下一行是否为复合表头的「子表头行」。

    判据（附件6 实测：{利润定额, 实际利润} 循环）：非空取值去重后 ≤4 个、
    全部是非数字短文本、且至少 2 个非空。数据首行（如 A1/96.0/合格…）去重值多，不会误判。
    """
    idx = hdr_idx + 1
    if idx >= len(grid):
        return False
    vals = [_cell(v) for v in grid[idx]]
    non_empty = [v for v in vals if v != ""]
    if len(non_empty) < 2:
        return False
    distinct = set(non_empty)
    if len(distinct) > 4:
        return False
    if any(len(v) > 8 for v in distinct):
        return False
    if any(re.fullmatch(r"[-+]?\d+(\.\d+)?", v) for v in distinct):
        return False
    return True


def _header_strings(grid: list[list], hdr_idx: int, sub_idx: int | None) -> list[str]:
    """合成表头文本：主表头 + （若有）子表头拼接，便于列级识别。"""
    hdr = [_cell(v) for v in grid[hdr_idx]]
    if sub_idx is None:
        return hdr
    sub = [_cell(v) for v in grid[sub_idx]]
    out = []
    for i, h in enumerate(hdr):
        s = sub[i] if i < len(sub) else ""
        out.append(f"{h}.{s}" if h and s else (h or s))
    return out


def _month_groups(grid: list[list], hdr_idx: int, merges: list) -> list[dict]:
    """解析「月份 → 列区间」分组。

    优先用表头行的 merged_cells 归属（附件6 每月 3 列的权威来源）；
    无合并信息时用月份标签间距推断（附件1/2/3/4/5）。
    返回 [{month, cols, start}]，cols 为 0-based 列索引列表。
    """
    header = [_cell(v) for v in grid[hdr_idx]]
    ncols = len(header)

    def _month_of(text: str):
        m = _MONTH_LABEL_RE.match(text or "")
        if m and 1 <= int(m.group(1)) <= 12:
            return int(m.group(1))
        return None

    spans = sorted((clo, chi) for (rlo, rhi, clo, chi) in merges
                   if rlo == hdr_idx and chi - clo >= 1)
    groups: list[dict] = []
    if spans:
        # 每个合并区给出该月的起始列；组右界 = 下一个月起始列（附件6 每月 3 列，
        # 但合并区常只盖住前 2 列，第 3 列「评级」不在合并内 → 必须延伸到下一月起。
        starts: list[tuple[int, int]] = []
        for clo, chi in spans:
            month = next((_month_of(header[c]) for c in range(clo, min(chi, ncols))
                          if _month_of(header[c])), None)
            if month is None:
                continue
            starts.append((clo, month))
        if starts:
            for i, (clo, month) in enumerate(starts):
                end = starts[i + 1][0] if i + 1 < len(starts) else ncols
                groups.append({"month": month,
                               "cols": list(range(clo, max(clo + 1, end))),
                               "start": clo})
            return groups

    positions = [(i, _month_of(v)) for i, v in enumerate(header)]
    positions = [(i, m) for i, m in positions if m]
    if not positions:
        return []
    diffs = [b[0] - a[0] for a, b in zip(positions, positions[1:]) if b[0] > a[0]]
    spacing = Counter(diffs).most_common(1)[0][0] if diffs else 1
    for i, m in positions:
        groups.append({"month": m, "cols": list(range(i, min(i + max(1, spacing), ncols))),
                       "start": i})
    return groups


def _infer_role(sub_name: str, values: list, aliases: dict) -> str:
    """推断列角色：子表头优先；否则按取值（合格/不合格 → 评级，数值 → 得分）。"""
    if sub_name:
        for item in aliases.get("subs", []):
            if item.get("match") and item["match"] in sub_name:
                return item.get("role", sub_name)
        return sub_name
    texts = [str(v).strip() for v in values if isinstance(v, str) and v.strip()]
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if texts and not nums and all(t in ("合格", "不合格") for t in texts):
        return "评级"
    if nums:
        return "得分"
    return ""


def _table_meta(source_file: str, aliases: dict) -> dict:
    """按源文件名取表级元数据（table_id / canonical base / unit / metric）。"""
    for item in aliases.get("tables", []):
        if item.get("match") and item["match"] in source_file:
            return item
    return {}


def _canonical_of(meta: dict, role: str, month: int | None,
                  aliases: dict) -> str:
    """由表级 base + 角色 + 月份拼 canonical（如 profit_actual_m07）。"""
    base = meta.get("canonical", "") or "unknown"
    if role:
        for item in aliases.get("subs", []):
            if item.get("role") == role and item.get("canonical"):
                base = item["canonical"] if role in ("利润定额", "实际利润") \
                    else f"{meta.get('canonical', 'x')}_{item['canonical']}"
                break
    return f"{base}_m{month:02d}" if month else base


def _col_letter(idx: int) -> str:
    """0-based 列索引 → Excel 列字母（支持多字母，如 35→AJ）。"""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _col_index(letter: str) -> int:
    """Excel 列字母 → 0-based 列索引（支持多字母，如 AJ→35）；非法返回 -1。"""
    idx = 0
    for ch in (letter or "").strip().upper():
        if not ("A" <= ch <= "Z"):
            return -1
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1 if idx else -1


def _rows_have_data(rows: list[list], cols: list[int]) -> list[int]:
    """返回「在这些列上有非空值」的行号（0-based，相对 rows）。"""
    out = []
    for i, row in enumerate(rows):
        if any(_cell(row[c]) != "" for c in cols if c < len(row)):
            out.append(i)
    return out


def _parse_sheet(table_id: str, source_file: str, sheet: dict,
                 aliases: dict) -> dict:
    """解析单个 sheet：字段记录 / 表级三口径行数 / 未映射列 / 脏单元格 / 列画像。"""
    grid, merges = sheet["grid"], sheet["merges"]
    ncols = max((len(r) for r in grid), default=0)
    hdr_idx = _find_header_row(grid)
    sub_idx = (hdr_idx + 1
               if _looks_like_sub_header(grid[hdr_idx + 1] if hdr_idx + 1 < len(grid)
                                         else [], hdr_idx, grid) else None)
    data_start = (sub_idx + 1) if sub_idx is not None else hdr_idx + 1
    title = " ".join(dict.fromkeys(_cell(v) for row in grid[:hdr_idx]
                                  for v in row if _cell(v) != ""))
    header = [_cell(v) for v in grid[hdr_idx]]
    sub = [_cell(v) for v in grid[sub_idx]] if sub_idx is not None else []
    data_rows = grid[data_start:]
    meta = _table_meta(source_file, aliases)
    tmpl = aliases.get("name_templates", {})
    blank_as_zero = any(h.get("pattern") and h["pattern"] in title
                        for h in aliases.get("fillna_hints", []))
    fillna_rule = next((h.get("rule", "") for h in aliases.get("fillna_hints", [])
                        if h.get("pattern") and h["pattern"] in title), "")
    groups = _month_groups(grid, hdr_idx, merges)

    # 参与「行是否有数据」判定的列：门店列 + 月份组列
    first_start = groups[0]["start"] if groups else min(1, ncols)
    id_cols = list(range(0, first_start))
    group_cols = [c for g in groups for c in g["cols"]]
    mapped_cols = sorted(set(id_cols) | set(group_cols))
    rows_with_data = _rows_have_data(data_rows, mapped_cols)
    physical_rows = len(data_rows)
    effective_rows = len(rows_with_data)
    blank_rows = physical_rows - effective_rows

    fields: list[dict] = []
    profiles: list[dict] = []

    def _add_field(col: int, name: str, role: str, month: int | None):
        vals = [row[col] if col < len(row) else None for row in data_rows]
        non_empty = [v for v in vals if _cell(v) != ""]
        samples = []
        for v in non_empty:
            if _cell(v) not in samples:
                samples.append(_cell(v))
            if len(samples) >= MAX_SAMPLES:
                break
        raw_missing = round(1 - len(non_empty) / physical_rows, 4) if physical_rows else 1.0
        fill_zero = blank_as_zero and _infer_dtype(non_empty) in ("int", "float")
        # 整列全空但表标题声明「空格处为0」→ 视为 0 值列（不是缺失），避免 100% 缺失率流到下游
        all_blank_zero = bool(blank_as_zero and not non_empty)
        # 低基数文本列（如 N月.评级、门店列）记录值计数，供 golden 计数断言（Tess/Vera）
        distinct_vals = list(dict.fromkeys(_cell(v) for v in non_empty))
        value_counts: dict = {}
        if len(distinct_vals) <= VALUE_COUNTS_MAX_DISTINCT or role == "store_id":
            value_counts = dict(Counter(_cell(v) for v in non_empty).most_common())
        fields.append({
            "table_id": table_id,
            "table_name": meta.get("metric", ""),
            "sheet_name": sheet["sheet_name"],
            "table_title": title,
            "field_raw": header[col] if col < len(header) else "",
            "field_name": name,
            "field_norm": _norm_field(name),
            "role": role,
            "dtype": _infer_dtype(non_empty),
            "unit": meta.get("unit", ""),
            "period": f"M{month:02d}" if month else "",
            "entity_scope": "门店" if "门店" in f"{title} {meta.get('metric', '')}" else "",
            "canonical": _canonical_of(meta, role, month, aliases),
            "canonical_base": meta.get("canonical", ""),
            "row_count": effective_rows,
            "physical_rows": physical_rows,
            "effective_rows": effective_rows,
            "blank_rows": blank_rows,
            "missing_rate": 0.0 if (fill_zero or all_blank_zero) else raw_missing,
            "raw_missing_rate": raw_missing,
            "blank_as_zero": bool(fill_zero or all_blank_zero),
            "all_blank_as_zero": all_blank_zero,
            "merge_cells": [f"{rlo}:{rhi},{clo}:{chi}" for (rlo, rhi, clo, chi) in merges],
            "sample_values": samples,
            "value_counts": value_counts,
            "source_file": source_file,
            "source_column": _col_letter(col),
            "dup_group": "",
        })
        profiles.append({
            "table_id": table_id, "sheet": sheet["sheet_name"], "field_name": name,
            "role": role, "column": _col_letter(col), "dtype": _infer_dtype(non_empty),
            "non_empty": len(non_empty), "distinct": len(set(map(_cell, non_empty))),
            "signature": "|".join(sorted(set(map(_cell, non_empty)))[:8]),
        })

    # 1) 个月前导的标识列（门店列）
    for c in id_cols:
        vals = [row[c] if c < len(row) else None for row in data_rows]
        if not any(_cell(v) != "" for v in vals):
            continue
        name = header[c] or tmpl.get("store_column", "月份门店")
        f_meta = {"canonical": "store_id"}
        saved, meta = meta, f_meta  # 门店列 canonical 固定为 store_id
        _add_field(c, name, "store_id", None)
        meta = saved
        fields[-1]["canonical"] = "store_id"
        fields[-1]["unit"] = ""
        fields[-1]["period"] = ""

    # 2) 月份组列（单列 → N月；多列 → N月.<role>）
    for g in groups:
        month = g["month"]
        cols = g["cols"]
        multi = len(cols) > 1
        roles = {}
        for c in cols:
            vals = [row[c] if c < len(row) else None for row in data_rows]
            # 仅多列月份组才按「得分/评级」推断角色；单列月度列角色留空（如附件4 投诉量）
            roles[c] = _infer_role(sub[c] if c < len(sub) else "", vals, aliases) if multi else ""
        for c in cols:
            role = roles[c]
            if len(cols) == 1:
                name = str(tmpl.get("month", "{month}月")).format(month=month)
            else:
                name = str(tmpl.get("month_metric", "{month}月.{role}")).format(
                    month=month, role=role or "值")
            _add_field(c, name, role, month)

    # 3) 月份组之外但下方有数据的列 → 未映射列留痕（禁止静默丢弃）
    last_end = (max(g["cols"][-1] for g in groups) + 1) if groups else first_start
    unmapped: list[dict] = []
    for c in range(last_end, ncols):
        vals = [row[c] if c < len(row) else None for row in data_rows]
        non_empty = [v for v in vals if _cell(v) != ""]
        if not non_empty:
            continue
        note = "表头为空且有数据，未纳入字段表"
        canonical_hint = ""
        derived = False
        # 派生列判定：该列每个非空值 ≈ 同一行各月列的算术均值（如附件1 的 N 列）
        try:
            checked = matched = 0
            for row in data_rows:
                rv = row[c] if c < len(row) else None
                if _cell(rv) == "":
                    continue
                nums = [float(row[cc]) for cc in group_cols
                        if cc < len(row) and isinstance(row[cc], (int, float))
                        and not isinstance(row[cc], bool)]
                if not nums:
                    continue
                checked += 1
                if abs(sum(nums) / len(nums) - float(rv)) < 0.05:
                    matched += 1
            if checked and matched == checked:
                note = "派生列（= 该门店各月列算术均值，与源文件既有列一致）"
                canonical_hint = meta.get("canonical", "")
                derived = True
        except (TypeError, ValueError):
            pass
        unmapped.append({
            "table_id": table_id, "source_file": source_file,
            "sheet": sheet["sheet_name"], "column": _col_letter(c),
            "header_raw": header[c] if c < len(header) else "",
            "non_empty": len(non_empty), "sample_values": [str(v) for v in non_empty[:3]],
            "note": note, "derived": derived, "canonical_hint": canonical_hint,
        })

    # 4) 脏单元格（文本混入数值列，如 '97.3.4'）——只留痕，不改值
    dirty: list[dict] = []
    for f in fields:
        col = f["source_column"]
        c = _col_index(col)
        if c < 0 or f["dtype"] != "str":
            continue
        vals = [row[c] if c < len(row) else None for row in data_rows]
        strs = [_cell(v) for v in vals if _cell(v) != ""]
        if not strs:
            continue
        numericish = sum(1 for s in strs if re.fullmatch(r"[-+]?\d+(\.\d+)?", s))
        if numericish / len(strs) < 0.5:
            continue
        for i, v in enumerate(vals):
            s = _cell(v)
            if _MULTIDOT_RE.match(s):
                dirty.append({
                    "table_id": table_id, "source_file": source_file,
                    "sheet": sheet["sheet_name"],
                    "excel_row": data_start + i + 1, "column": col,
                    "field_name": f["field_name"], "value": s,
                    "store": _cell(data_rows[i][0]) if data_rows[i] else "",
                })

    # 5) 重复列组（逐行全等）标记 dup_group——只留痕，不去重
    col_values = {}
    for f in fields:
        c = _col_index(f["source_column"])
        if c >= 0:
            col_values[f["field_name"]] = tuple(
                _cell(row[c]) if c < len(row) else "" for row in data_rows)
    for role, prefix in (("利润定额", "def"), ("实际利润", "act"), ("评级", "qual")):
        fs = [f for f in fields if f["role"] == role]
        buckets: dict[tuple, list[dict]] = defaultdict(list)
        for f in fs:
            buckets[col_values.get(f["field_name"], ())].append(f)
        for vals, group in buckets.items():
            if len(group) < 2 or not any(vals):
                continue
            months = [int(f["period"][1:]) for f in group if f["period"]]
            label = f"{prefix}_{'_'.join(str(m) for m in sorted(months))}"
            for f in group:
                f["dup_group"] = label

    # 6) 表级数值列合计（供 golden 值断言；空值不计入，填 0 口径不改变合计）
    numeric_sum: dict[str, float] = {}
    for f in fields:
        if f["dtype"] not in ("int", "float"):
            continue
        c = _col_index(f["source_column"])
        tot = 0.0
        for row in data_rows:
            v = row[c] if 0 <= c < len(row) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                tot += float(v)
        numeric_sum[f["field_name"]] = round(tot, 4)

    return {
        "table_id": table_id, "source_file": source_file,
        "sheet_name": sheet["sheet_name"], "table_title": title,
        "table_name": meta.get("metric", ""), "merge_cell_count": len(merges),
        "physical_rows": physical_rows, "effective_rows": effective_rows,
        "blank_rows": blank_rows,
        "row_count": effective_rows, "field_count": len(fields),
        "header_rows": (sub_idx + 1 if sub_idx is not None else hdr_idx + 1) - 0,
        "fillna_rule": fillna_rule, "blank_as_zero": blank_as_zero,
        "numeric_sum": numeric_sum,
        "fields": fields, "profiles": profiles,
        "unmapped": unmapped, "dirty": dirty,
    }


# ---------------------------------------------------------------- 冲突检测

def _mk_conflict(cid: str, ctype: str, field_base: str, variants: list,
                 tables: list, evidence_rows: list, suggested: str,
                 message: str, evidence: list | None = None) -> dict:
    """构造冲突记录：含文档要求的必需键（见 CONFLICT_KEYS）。"""
    return {
        # 必需键
        "conflict_id": cid,
        "conflict_type": ctype,
        "field_base": field_base,
        "variants": variants,
        "tables": tables,
        "evidence_rows": evidence_rows,
        "suggested_caliber": suggested,
        # 兼容/人读
        "evidence": evidence or [],
        "message": message,
    }


def _row_evidence(fields: list[dict], records_by_key: dict, limit: int = 3) -> list[dict]:
    """取代表性行证据（前 limit 个有效行，含 Excel 行号与门店 id）。"""
    out = []
    for f in fields[:limit]:
        src = records_by_key.get((f["table_id"], f["sheet_name"]))
        rows = src["data_rows"] if src else []
        c = _col_index(f["source_column"])
        for i, row in enumerate(rows[:limit]):
            out.append({
                "source_file": f["source_file"], "sheet": f["sheet_name"],
                "excel_row": src["data_start"] + i + 1 if src else None,
                "field_name": f["field_name"],
                "store": _cell(row[0]) if row else "",
                "value": (_cell(row[c]) if (row and 0 <= c < len(row)) else ""),
            })
    return out


def _detect_conflicts(fields: list[dict], dirty: list[dict],
                      records_by_key: dict) -> list[dict]:
    """口径冲突检测：definition / unit / period / entity_scope。

    以「字段角色」（role，如 评级/得分/利润定额/空=单列月份）为分组键跨附件比对：
    - 同角色跨文件出现且各文件**指标不同** → definition（同名不同义 / 不同考核定义）；
    - 同角色跨文件且 **dtype 不一致**（尤其脏数据导致退化） → definition（类型冲突）；
    - 同名字段跨文件 **unit 不一致** → unit 冲突；
    - 同名字段跨文件 period / entity_scope 不一致 → 对应冲突。
    每条给出 evidence_rows（Excel 行号 + 门店 + 值）。
    """
    conflicts: list[dict] = []
    by_role: dict[str, list[dict]] = defaultdict(list)
    for f in fields:
        if f["role"] == "store_id":
            continue
        by_role[f["role"]].append(f)

    seq = 0
    for role, fs in by_role.items():
        files = {f["source_file"] for f in fs}
        if len(files) < 2:
            continue
        seq += 1
        metrics = sorted({f["table_name"] or f["source_file"] for f in fs})
        units = sorted({f["unit"] or "(无单位)" for f in fs})
        dtypes = sorted({f["dtype"] for f in fs} - {"empty"})
        # 粗类型：int/float 同属数值，只有「数值 ↔ 文本」才视为真 dtype 冲突，
        # 避免把 int/float 的等价差异误报为口径冲突。
        coarse = sorted({("num" if d in ("int", "float") else d)
                         for d in dtypes})
        tables = sorted({f["table_id"] for f in fs})
        names = sorted({f["field_name"] for f in fs})
        base = role or "N月（单列月份）"

        # ① dtype 冲突（含脏数据导致的文本混入）
        if len(coarse) > 1:
            rel_dirty = [d for d in dirty if d["field_name"] in names]
            ev_rows = [{"source_file": d["source_file"], "sheet": d["sheet"],
                        "excel_row": d["excel_row"], "field_name": d["field_name"],
                        "store": d["store"], "value": d["value"]} for d in rel_dirty[:8]]
            detail = f"；其中文本混入单元格 {len(rel_dirty)} 处（如 {rel_dirty[0]['value']!r}）" \
                if rel_dirty else ""
            conflicts.append(_mk_conflict(
                f"C{seq:03d}", "definition", base, dtypes, tables,
                ev_rows or _row_evidence(fs, records_by_key),
                "确认数值列类型（清理文本混入值或统一为字符串）后重算",
                f"同名字段「{base}」跨附件 dtype 不一致：{' / '.join(dtypes)}"
                f"（涉及 {', '.join(m.split(' ')[0] for m in metrics)}）{detail}"))
            # 同组若单位也不一致，另记一条
            if len(units) > 1:
                seq += 1
                conflicts.append(_mk_conflict(
                    f"C{seq:03d}", "unit", base, units, tables,
                    _row_evidence(fs, records_by_key),
                    "统一单位或标注换算系数", 
                    f"同名字段「{base}」跨附件单位不一致：{' / '.join(units)}"))
            continue

        # ② 同名不同义 / 不同考核定义（跨附件指标不同）
        if len(metrics) > 1:
            seq += 1
            conflicts.append(_mk_conflict(
                f"C{seq:03d}", "definition", base, metrics, tables,
                _row_evidence(fs, records_by_key),
                "按各自考核定义独立建模；若需合并须先统一口径",
                f"同名字段「{base}」在 {'、'.join(metrics)} 中是不同的考核/统计口径"
                f"（同名不同义），跨表按名合并会得出错误结论"))

        # ③ unit 冲突
        if len(units) > 1:
            seq += 1
            conflicts.append(_mk_conflict(
                f"C{seq:03d}", "unit", base, units, tables,
                _row_evidence(fs, records_by_key),
                "统一单位或标注换算系数",
                f"同名字段「{base}」跨附件单位不一致：{' / '.join(units)}"))

    # ④ 表级单位只在标题声明（字段层无单位）——附件6 万元
    by_sheet: dict[tuple, list[dict]] = defaultdict(list)
    for f in fields:
        by_sheet[(f["table_id"], f["sheet_name"])].append(f)
    for (tid, sheet), fs in by_sheet.items():
        title = fs[0].get("table_title", "")
        m = _UNIT_RE.search(title)
        title_unit = m.group(1) if (m and _looks_like_unit(m.group(1))) else ""
        field_units = {f["unit"] for f in fs if f["unit"]}
        if title_unit and not field_units and len(fs) > 1:
            seq += 1
            conflicts.append(_mk_conflict(
                f"C{seq:03d}", "unit", f"<表级单位:{title_unit}>", [title_unit],
                [tid], [], f"字段层补单位「{title_unit}」或统一换算",
                f"表 {tid}/{sheet} 的单位「{title_unit}」只声明在表标题、字段层无单位"))

    # ⑤ period / entity_scope 冲突（同名字段跨文件）
    for key, ctype, label in (("period", "period", "统计周期"),
                              ("entity_scope", "entity_scope", "实体范围")):
        groups: dict[str, set] = defaultdict(set)
        for f in fields:
            if not f[key] or f["role"] == "store_id":
                continue
            groups[f["field_name"]].add(f[key])
        for name, vals in groups.items():
            if len(vals) < 2:
                continue
            seq += 1
            fs = [f for f in fields if f["field_name"] == name]
            conflicts.append(_mk_conflict(
                f"C{seq:03d}", ctype, name, sorted(vals),
                sorted({f["table_id"] for f in fs}),
                _row_evidence(fs, records_by_key),
                f"统一{label}后再比对", f"同名字段「{name}」{label}不一致：{' / '.join(sorted(vals))}"))
    return conflicts


# ---------------------------------------------------------------- 主入口

def _in_scope(source_file: str, scope: dict) -> bool:
    """按输入白名单判断文件是否纳入体检（skip 优先于 include）。"""
    if any(k and k in source_file for k in scope.get("skip_keywords", [])):
        return False
    includes = scope.get("include", [])
    return any(k and k in source_file for k in includes) if includes else True


def run_intake_audit(dataset_dir: str | Path, out_dir: str | Path) -> dict:
    """遍历黄金输入（白名单）执行附件体检，产出体检产物并返回汇总 dict。"""
    dataset_dir, out_dir = Path(dataset_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    aliases = _load_aliases()
    scope = _load_scope()

    all_fields: list[dict] = []
    tables: list[dict] = []
    profiles: list[dict] = []
    unmapped: list[dict] = []
    dirty: list[dict] = []
    skipped: list[dict] = []
    records_by_key: dict[tuple, dict] = {}

    files = sorted(p for p in dataset_dir.rglob("*")
                   if p.suffix.lower() in (".xls", ".xlsx"))
    for path in files:
        if not _in_scope(path.name, scope):
            skipped.append({"file": path.name, "reason": SKIP_REASON})
            continue
        meta = _table_meta(path.name, aliases)
        table_id = meta.get("table_id") or f"T{len(tables) + 1:02d}"
        sheets = _read_xls(path) if path.suffix.lower() == ".xls" else _read_xlsx(path)
        for sheet in sheets:
            if not sheet["grid"]:
                continue
            parsed = _parse_sheet(table_id, path.name, sheet, aliases)
            if not parsed["fields"]:
                continue
            all_fields.extend(parsed["fields"])
            profiles.extend(parsed["profiles"])
            unmapped.extend(parsed["unmapped"])
            dirty.extend(parsed["dirty"])
            records_by_key[(table_id, parsed["sheet_name"])] = {
                "data_rows": sheet["grid"][parsed["header_rows"]:],
                "data_start": parsed["header_rows"],
            }
            tables.append({k: v for k, v in parsed.items()
                           if k not in ("fields", "profiles", "unmapped", "dirty")})

    conflicts = _detect_conflicts(all_fields, dirty, records_by_key)

    # 1) schema_report.json（顶层附 skipped_files 留痕，供白名单断言）
    (out_dir / "schema_report.json").write_text(json.dumps(
        {"tables": tables, "fields": all_fields, "skipped_files": skipped},
        ensure_ascii=False, indent=2), encoding="utf-8")
    # 1b) skipped_files.json（独立留痕，便于 grep）
    (out_dir / "skipped_files.json").write_text(json.dumps(
        {"skipped_files": skipped, "count": len(skipped)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    # 2) caliber_conflicts.json
    (out_dir / "caliber_conflicts.json").write_text(json.dumps(
        {"conflicts": conflicts, "count": len(conflicts)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    # 3) field_mapping.csv
    with (out_dir / "field_mapping.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["table_id", "field_name", "field_raw", "field_norm", "canonical",
                    "role", "unit", "period", "entity_scope", "fillna_rule",
                    "blank_as_zero", "dup_group", "dtype", "row_count",
                    "physical_rows", "effective_rows", "blank_rows", "missing_rate",
                    "raw_missing_rate", "source_file", "source_sheet",
                    "confirmed_by", "confirmed_at"])
        for r in all_fields:
            t_rule = next((t["fillna_rule"] for t in tables
                           if t["table_id"] == r["table_id"]
                           and t["sheet_name"] == r["sheet_name"]), "")
            w.writerow([r["table_id"], r["field_name"], r["field_raw"], r["field_norm"],
                        r["canonical"], r["role"], r["unit"], r["period"],
                        r["entity_scope"], t_rule,
                        int(r["blank_as_zero"]), r["dup_group"], r["dtype"],
                        r["row_count"], r["physical_rows"], r["effective_rows"],
                        r["blank_rows"], r["missing_rate"], r["raw_missing_rate"],
                        r["source_file"], r["sheet_name"], "", ""])
    # 4) 留痕清单
    (out_dir / "unmapped_columns.json").write_text(json.dumps(
        {"unmapped_columns": unmapped, "count": len(unmapped)},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "dirty_cells.json").write_text(json.dumps(
        {"dirty_cells": dirty, "count": len(dirty)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "column_profiles.json").write_text(json.dumps(
        {"profiles": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")
    # 5) intake_summary.md
    _write_summary(out_dir, dataset_dir, tables, all_fields, conflicts,
                   unmapped, dirty, skipped)

    return {"tables": tables, "field_count": len(all_fields),
            "conflict_count": len(conflicts), "conflicts": conflicts,
            "unmapped_count": len(unmapped), "dirty_cell_count": len(dirty),
            "skipped_files": skipped, "out_dir": str(out_dir)}


def _write_summary(out_dir: Path, dataset_dir: Path, tables: list, fields: list,
                   conflicts: list, unmapped: list, dirty: list,
                   skipped: list) -> None:
    """写人读摘要 intake_summary.md。"""
    # 缺失率按 post-fillna 口径排序（整列空但声明「空格处为0」的列不算缺失，不占榜首）
    top_missing = sorted(fields, key=lambda r: r["missing_rate"], reverse=True)[:10]
    dup_groups = sorted({f["dup_group"] for f in fields if f["dup_group"]})
    lines = ["# 附件体检摘要", "",
             f"- 数据集目录：`{dataset_dir}`",
             f"- 表(sheet)数：{len(tables)}；字段数：{len(fields)}",
             f"- 口径冲突：{len(conflicts)} 条；未映射列：{len(unmapped)} 条；脏单元格：{len(dirty)} 处",
             f"- 输入白名单跳过：{len(skipped)} 个文件"
             + (f"（{', '.join(s['file'] for s in skipped)}；{SKIP_REASON}）"
                if skipped else ""), "",
             "## 各表行数（三口径）", "",
             "| table_id | 文件 | sheet | 标题 | 物理行 | 有效行 | 空行 | 字段数 |",
             "|---|---|---|---|---|---|---|---|"]
    for t in tables:
        lines.append(f"| {t['table_id']} | {t['source_file']} | {t['sheet_name']} "
                     f"| {t.get('table_title', '')} | {t['physical_rows']} "
                     f"| {t['effective_rows']} | {t['blank_rows']} | {t['field_count']} |")
    lines += ["", "## 缺失率 Top10（post-fillna 口径）", "",
              "| table_id | 字段 | 缺失率 | 原始缺失率 | blank_as_zero | all_blank_as_zero |",
              "|---|---|---|---|---|---|"]
    for r in top_missing:
        lines.append(f"| {r['table_id']} | {r['field_name']} | {r['missing_rate']:.2%} "
                     f"| {r['raw_missing_rate']:.2%} | {r['blank_as_zero']} "
                     f"| {r.get('all_blank_as_zero', False)} |")
    abz = [f for f in fields if f.get("all_blank_as_zero")]
    if abz:
        lines += ["", "含**整列全空但按 0 计**的列（表标题声明「空格处为0」，"
                  "已标记 `all_blank_as_zero=true`，不作为缺失）：", ""]
        for r in abz:
            lines.append(f"- `{r['table_id']}/{r['sheet_name']}` 字段 "
                         f"`{r['field_name']}`（{r['field_raw']}）：整列空，按 0 计")

    lines += ["", "## 口径来源说明（fillna_rule）", ""]
    zero_tables = sorted({f"{t['table_id']}/{t['sheet_name']}" for t in tables
                          if t.get("blank_as_zero")})
    if zero_tables:
        lines.append("以下表的**空缺值不是缺失**，而是按表标题声明的语义填 0"
                     "（数值列 missing_rate 已按填 0 口径记 0，raw_missing_rate 保留原值）：")
        for key in zero_tables:
            t = next(x for x in tables if f"{x['table_id']}/{x['sheet_name']}" == key)
            lines.append(f"- `{key}`：{t['fillna_rule']}；口径来源＝表标题「{t['table_title']}」")
    else:
        lines.append("- 未发现表标题声明的空缺语义。")

    lines += ["", "## 未映射列留痕", ""]
    if unmapped:
        for u in unmapped:
            lines.append(f"- {u['source_file']} / {u['sheet']} / 列 {u['column']}"
                         f"（表头 {u['header_raw']!r}，{u['non_empty']} 个非空）：{u['note']}"
                         + (f"；canonical 提示={u['canonical_hint']}" if u['canonical_hint'] else ""))
    else:
        lines.append("- （无）")

    lines += ["", "## 脏单元格（文本混入数值列，未自动改正）", ""]
    if dirty:
        for d in dirty[:20]:
            lines.append(f"- {d['source_file']} / {d['sheet']} / 行 {d['excel_row']} "
                         f"列 {d['column']}（{d['field_name']}）：值 {d['value']!r}"
                         + (f" 门店={d['store']}" if d['store'] else ""))
        if len(dirty) > 20:
            lines.append(f"- …共 {len(dirty)} 处，完整清单见 dirty_cells.json")
    else:
        lines.append("- （无）")

    lines += ["", "## 重复列组（逐行全等，仅留痕不去重）", ""]
    if dup_groups:
        for g in dup_groups:
            members = sorted({f["field_name"] for f in fields if f["dup_group"] == g})
            lines.append(f"- `{g}`：{', '.join(members)}")
        lines.append("")
        lines.append("**已知数据问题（风险）**：附件6 的「利润定额」存在逐行全等的月份组"
                     "（同定额），而「实际利润」无全等组 → 若对每月独立回归，"
                     "定额共线可能给出虚假显著系数；请不要去重，改在建模时按分组或差分处理。")
    else:
        lines.append("- （无）")

    lines += ["", "## 已知数据问题 / 解析纪律", "",
              "1. **跨表汇总必须按门店 id join，禁止按行号对齐**：附件1 的门店顺序与其他"
              "附件不同（A1..A5,B14,B37,B70,B56,B108,B7,B91… 乱序），附件2~6 为 "
              "A1..A5,B1..B7 顺序。",
              "2. 附件4/5 表标题明写「空格处为0」：空缺即 0（见 fillna_rule 段）。",
              "3. 附件6 定额列存在逐行全等月份组（见重复列组段），建模时注意共线。",
              "4. 附件3 有文本混入数值列的脏单元格（见脏单元格段），未自动改正，需业务确认。", "",
              "## 口径冲突清单", ""]
    if conflicts:
        for c in conflicts:
            lines.append(f"- **[{c['conflict_type']}]** {c['message']}")
            lines.append(f"  - id={c['conflict_id']}；建议口径：{c['suggested_caliber']}")
            for ev in c["evidence_rows"][:3]:
                lines.append(f"  - 证据行：{ev.get('source_file', '')} / "
                             f"{ev.get('sheet', '')} / 行 {ev.get('excel_row', '')} / "
                             f"{ev.get('field_name', '')} = {ev.get('value', '')!r}"
                             + (f"（门店 {ev['store']}）" if ev.get("store") else ""))
    else:
        lines.append("- （未检出）")
    (out_dir / "intake_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
