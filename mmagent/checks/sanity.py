"""FR-5 理智性检查：对结果表跑内置规则 + 自定义规则，产出 SanityReport。

内置规则（按列名关键词触发）：
- 得分/score           → 取值 0~100（blocking）
- 占比/ratio/proportion → 取值 0~1（blocking）
- 利润/profit          → ≥0（blocking）
- 行数一致：rules 中 {"name": "row_count_match", "tables": [...]}（blocking）
- 主键唯一：rules 中 {"name": "unique", "table": ..., "column": ...}（blocking）

自定义 rules 条目：
{"name": "range", "table": ..., "column": ..., "min": .., "max": .., "blocking": bool}
{"name": "not_null", "table": ..., "column": ...}
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .units import check_units

# (关键词元组, min, max)
_BUILTIN_RANGES = [
    (("得分", "score"), 0, 100),
    (("占比", "ratio", "proportion", "比例"), 0, 1),
    (("利润", "profit"), 0, None),
]


@dataclass
class SanityReport:
    passed: bool
    violations: list[dict] = field(default_factory=list)
    blocking: bool = False


def _violation(rule: str, table: str, column: str, rows: list,
               message: str, blocking: bool) -> dict:
    return {"rule": rule, "table": table, "column": column,
            "rows": rows[:20],  # 只留前 20 行号，避免报告膨胀
            "message": message, "blocking": blocking}


def _check_range(df, table: str, column: str, vmin, vmax, rule: str,
                 blocking: bool, violations: list) -> None:
    col = df[column]
    notna = col.notna()  # 空值不参与越界判定（先剔除，避免 NaN 被并入越界行号）
    bad = notna & False
    if vmin is not None:
        bad = bad | (notna & (col < vmin))
    if vmax is not None:
        bad = bad | (notna & (col > vmax))
    bad_rows = df.index[bad].tolist()
    if bad_rows:
        violations.append(_violation(
            rule, table, column, bad_rows,
            f"列 {column} 存在越界值（期望 {vmin}~{vmax}），共 {len(bad_rows)} 行",
            blocking))


def run_sanity_checks(result_tables: dict, rules: list[dict] | None = None) -> SanityReport:
    """对 result_tables {表名: DataFrame} 执行检查，返回 SanityReport。"""
    violations: list[dict] = []

    # ---- 内置关键词规则
    for table, df in result_tables.items():
        for column in df.columns:
            name = str(column).lower()
            for keywords, vmin, vmax in _BUILTIN_RANGES:
                if any(k.lower() in name for k in keywords):
                    try:
                        _check_range(df, table, column, vmin, vmax,
                                     "builtin_range", True, violations)
                    except TypeError:
                        pass  # 非数值列跳过
                    break

    # ---- 自定义规则
    for rule in rules or []:
        name = rule.get("name")
        blocking = bool(rule.get("blocking", True))
        if name == "range":
            table, column = rule["table"], rule["column"]
            if table in result_tables and column in result_tables[table].columns:
                _check_range(result_tables[table], table, column,
                             rule.get("min"), rule.get("max"),
                             "range", blocking, violations)
        elif name == "not_null":
            table, column = rule["table"], rule["column"]
            df = result_tables.get(table)
            if df is not None and column in df.columns:
                bad = df.index[df[column].isna()].tolist()
                if bad:
                    violations.append(_violation(
                        "not_null", table, column, bad,
                        f"列 {column} 存在 {len(bad)} 个空值", blocking))
        elif name == "unique":
            table, column = rule["table"], rule["column"]
            df = result_tables.get(table)
            if df is not None and column in df.columns:
                dup = df.index[df[column].duplicated(keep=False)].tolist()
                if dup:
                    violations.append(_violation(
                        "unique", table, column, dup,
                        f"主键列 {column} 存在 {len(dup)} 行重复", blocking))
        elif name == "row_count_match":
            tables = rule.get("tables", [])
            counts = {t: len(result_tables[t]) for t in tables if t in result_tables}
            if len(set(counts.values())) > 1:
                violations.append(_violation(
                    "row_count_match", ",".join(counts), "", [],
                    f"表间行数不一致：{counts}", blocking))

    # ---- 量纲检查（FR-5 / 规划书 §3.5.1第1项）：独立模块，规则走 rules 里 name=="units"
    unit_rules = [r for r in (rules or []) if r.get("name") == "units"]
    violations.extend(check_units(result_tables, unit_rules))

    blocking = any(v["blocking"] for v in violations)
    return SanityReport(passed=not violations, violations=violations, blocking=blocking)
