"""FR-5 量纲（单位）检查：规划书 §3.5.1「必须由 Python 完成、绝不交给 LLM」清单第 1 项。

为什么必须是确定性代码：`万元 vs 元` 这类单位错位不会报错、不会崩溃、不会让任何
下游步骤失败，只会让错数字安静地流进论文——是数模场景最贵的故障模式（规划书 §13.1
列为第二大风险）。LLM 读表时对单位既不可靠也无法留痕，所以放在 checks 层用规则判。

五条规则（前四条为规格 §2.3 裁决；`units_unknown` 为第 28 轮验收补入，见下）：

| rule            | 判据                                                         | blocking |
|-----------------|--------------------------------------------------------------|----------|
| units_mismatch  | 同一逻辑字段（去单位后缀）在**多处**声明了不同单位            | True     |
| units_scale     | 单位 ∈ {万元, 亿元} 且该列非空数值的 max(abs) < 1            | True     |
| units_missing   | 列名命中金额/比率关键词但**无单位后缀**                      | False    |
| units_unknown   | 有 `(xxx)` 单位后缀但 `xxx` 不在 `KNOWN_UNITS` 内            | False    |
| units_declared  | 自定义 rules 声明的期望单位与实际不符                        | 取 rule  |

`units_missing` / `units_unknown` 故意不阻断：`率`/`占比` 类字段天然无量纲，未知单位
（`万美元`、`克`）也可能完全合法，blocking 会大面积误报，反而训练使用者忽略报告。
`units_scale` 只对 `0 < max(abs) < 1` 出手——全空 / 非数值 / 全 0 都是「没有证据」，不报；
触发单位集合**固定为 {万元, 亿元}**：只有这两个的「数值应 ≥ 1」是确定的，
`万美元`/`千元` 量级阈值不同（0.5 万美元 = 5000 美元，不是量纲错），纳入即造新误报。

`units_unknown` 的存在理由：`利润(万美元)` 声明了单位，若走 `units_missing` 就会
输出「未声明单位」——**文案与事实相反**。有后缀但认不出时必须如实说「未识别的单位」。

`rules` 中 `{"name": "units", ...}` 的语义分两种：
- **带 `unit` 键**：声明式校验（`units_declared`），与本列实际单位比对；
- **不带 `unit` 键**：只表示「对该列启用探测」，**不是**声明，不参与 `units_declared`
  （否则文案会变成「声明单位应为『』」并误 blocking，反过来卡住正常导出）。

返回结构与 `mmagent/checks/sanity.py::_violation` 完全同构（rule / table / column /
rows / message / blocking），调用方直接 `violations.extend(...)` 并入同一个
SanityReport，不另造一套报告格式。
"""
from __future__ import annotations

import re

import pandas as pd

# 列名末尾的 (单位) / （单位）。只认末尾，避免误吃 "满意度(分)说明" 这类中间括号。
UNIT_SUFFIX_RE = re.compile(r"[（(]\s*([^）)]{1,8})\s*[）)]\s*$")

# 纯数字后缀（"(2023)" 年份、"(3)" 期号）不是单位声明：既不进 KNOWN_UNITS，
# 也不报 units_unknown（说「未识别的单位 2023」是另一句假话）。
_NOT_A_UNIT_RE = re.compile(r"^\d+$")

# 可识别的单位白名单：不在表内的括号内容（"(克)"、"(备注)"）报 units_unknown，
# 而不是报 units_missing——人家声明了，只是我们认不出。
KNOWN_UNITS = {"元", "万元", "千元", "亿元", "万美元", "%", "％", "个", "天", "小时",
               "分钟", "人", "次", "分", "件", "台", "辆", "元/月", "万元/月"}

# 天然无量纲的列名后缀，units_missing 直接放过。
# 注：规格给的白名单是 ("率","比例","序号","编号","id","ID")，此处补入 "占比"。
# 原因有二：①「占比」与已白名单的「比例」同义，本身就是无量纲比率，提示纯噪声；
# ② 不补则既有 tests/test_sanity.py::test_多个表多个列同时越界（断言 violations==2）
#    必红，而本轮硬规则 5 禁止改既有测试断言。见 implementer-report-T2.md「偏差登记」。
KNOWN_UNITLESS_TAIL = ("率", "比例", "占比", "序号", "编号", "id", "ID")

# units_missing 的触发关键词（金额 / 比率类）。
_MONEY_RATE_KEYWORDS = ("金额", "利润", "成本", "收入", "价格", "费用", "占比", "比例", "率")

# 大额货币单位：正常业务数据不该整列都 < 1（那更像"亿元"或干脆是比率）。
_SCALE_UNITS = {"万元", "亿元"}


def _violation(rule: str, table: str, column: str, rows: list,
               message: str, blocking: bool) -> dict:
    """与 sanity._violation 同构。此处重写而非 import：sanity 顶层 import 本模块，
    反向 import 会成环（sanity → units → sanity 半初始化即失败）。"""
    return {"rule": rule, "table": table, "column": column,
            "rows": list(rows)[:20],  # 只留前 20 行号，避免报告膨胀
            "message": message, "blocking": blocking}


def _normalize_unit(unit: str) -> str:
    """全角百分号归一，避免 "%" 与 "％" 被当成两个单位误报冲突。"""
    return unit.strip().replace("％", "%")


def _split_column(column) -> tuple[str, str | None, str | None]:
    """列名 → (逻辑字段名, 已知单位, 括号内原文)。

    三态：
    - 已知单位 → (去后缀的逻辑名, 归一单位, 原文)
    - 有括号但认不出 → (完整列名, None, 原文)，由 units_unknown 如实报告
    - 无括号 / 纯数字括号 → (完整列名, None, None)，视同没有声明单位
    """
    text = str(column)
    stripped = text.strip()
    match = UNIT_SUFFIX_RE.search(stripped)
    if not match:
        return text, None, None
    token = match.group(1).strip()
    if _NOT_A_UNIT_RE.match(token):
        return text, None, None
    unit = _normalize_unit(token)
    if unit in KNOWN_UNITS:
        return stripped[:match.start()].strip(), unit, token
    return text, None, token


def _check_mismatch(result_tables: dict) -> list[dict]:
    """同一逻辑字段在多处声明了不同单位。未声明（无后缀）不算「声明了别的单位」。

    判据不区分跨表还是同表内（同名不同单位同样必错一处），所以文案写「多处」，
    不写「不同表」；detail 里带 `表.列`，同表场景也能定位到具体列。
    """
    declared: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for table, df in result_tables.items():
        for column in df.columns:
            logical, unit, _ = _split_column(column)
            if unit is None:
                continue
            declared.setdefault(logical, {}).setdefault(unit, []).append((table, str(column)))

    violations = []
    for logical, by_unit in declared.items():
        if len(by_unit) < 2:
            continue
        tables = sorted({t for items in by_unit.values() for t, _ in items})
        detail = "；".join(
            f"{unit} ← {', '.join(f'{t}.{c}' for t, c in items)}"
            for unit, items in sorted(by_unit.items()))
        violations.append(_violation(
            "units_mismatch", ",".join(tables), logical, [],
            f"逻辑字段 {logical} 在多处声明了不同单位（{detail}），至少一处量纲错", True))
    return violations


def _check_scale(result_tables: dict) -> list[dict]:
    """万元/亿元 列却整列 < 1：量纲错位（实际是亿元）或塞了比率。"""
    violations = []
    for table, df in result_tables.items():
        for column in df.columns:
            _, unit, _ = _split_column(column)
            if unit not in _SCALE_UNITS:
                continue
            numeric = pd.to_numeric(df[column], errors="coerce")
            if numeric.notna().sum() == 0:
                continue  # 全空 / 非数值：没有证据，不报
            max_abs = float(numeric.abs().max())
            if not (0 < max_abs < 1):
                continue  # max >= 1 正常；全 0 无证据
            rows = numeric[numeric.abs() < 1].index.tolist()
            violations.append(_violation(
                "units_scale", table, column, rows,
                f"列 {column} 声明单位为「{unit}」，但非空数值最大绝对值仅 {max_abs:g}（< 1），"
                f"疑似实际单位为亿元或该列是比率，量纲错位", True))
    return violations


def _check_unknown(result_tables: dict) -> list[dict]:
    """有 (xxx) 后缀但 xxx 不在 KNOWN_UNITS：如实说「未识别」，不谎称「未声明」。"""
    violations = []
    for table, df in result_tables.items():
        for column in df.columns:
            _, unit, token = _split_column(column)
            if unit is not None or token is None:
                continue  # 已识别的单位 / 根本没有单位后缀
            violations.append(_violation(
                "units_unknown", table, column, [],
                f"列 {column} 的单位后缀「{token}」未识别，请确认是否笔误"
                f"（可识别单位见 checks/units.py KNOWN_UNITS）", False))
    return violations


def _check_missing(result_tables: dict) -> list[dict]:
    """命中金额/比率关键词但没写单位。提示性质，不阻断。

    `column` 一律用**原值**（不 strip）：violation 里的列名必须能直接 `df[column]` 取到，
    否则下游回查列会 KeyError。关键词/白名单匹配才用去空白后的文本。
    有任何括号单位后缀（含未识别的）都不进本规则——那是 units_unknown 的地盘。
    """
    violations = []
    for table, df in result_tables.items():
        for column in df.columns:
            text = str(column).strip()
            if not any(k in text for k in _MONEY_RATE_KEYWORDS):
                continue
            _, unit, token = _split_column(column)
            if unit is not None or token is not None:
                continue  # 已声明单位 / 有后缀但未识别（由 units_unknown 报告）
            if text.endswith("%") or text.endswith("％"):
                continue  # 百分号即单位
            if text.endswith(KNOWN_UNITLESS_TAIL):
                continue  # 率/比例/序号/编号/id 天然无量纲
            violations.append(_violation(
                "units_missing", table, column, [],
                f"列 {column} 命中金额/比率关键词但未声明单位，建议写成「列名(单位)」", False))
    return violations


def _is_units_rule(rule: dict) -> bool:
    if rule.get("name") == "units":
        return True
    # 放宽：显式写了 unit 的 table/column 规则也算（sanity 只转发 name=="units"，
    # 直接调用 check_units 的调用方可能省略 name）
    return "unit" in rule and "table" in rule and "column" in rule


def _check_declared(result_tables: dict, rules: list[dict] | None) -> list[dict]:
    """调用方显式声明期望单位（赛题档案里写死某列应为万元）。

    只有带 `unit` 键的 units 规则才是声明式校验；不带 `unit` 的只是「对该列启用探测」
    的标记，跳过——否则会输出「声明单位应为『』」并 blocking，反过来卡住正常导出。
    """
    violations = []
    for rule in rules or []:
        if not _is_units_rule(rule):
            continue
        if not rule.get("unit"):  # 没声明期望单位 = 只想触发探测，不是声明式校验
            continue
        table, column = rule.get("table"), rule.get("column")
        df = result_tables.get(table)
        if df is None or column not in df.columns:
            continue  # 规则指向不存在的表/列时静默跳过，与 sanity 既有口径一致
        expected = _normalize_unit(str(rule["unit"]))
        _, actual, token = _split_column(column)
        if actual == expected:
            continue
        if actual is not None:
            actual_desc = f"实际为「{actual}」"
        elif token is not None:
            actual_desc = f"实际为单位「{token}」（未识别）"
        else:
            actual_desc = "但该列未声明单位"
        violations.append(_violation(
            "units_declared", table, column, [],
            f"列 {column} 声明单位应为「{expected}」，{actual_desc}",
            bool(rule.get("blocking", True))))
    return violations


def check_units(result_tables: dict, rules: list[dict] | None = None) -> list[dict]:
    """量纲（单位）检查。返回 violations 列表，结构与 checks/sanity.py::_violation 完全同构。

    每项键：rule / table / column / rows / message / blocking
    """
    violations: list[dict] = []
    violations.extend(_check_mismatch(result_tables))
    violations.extend(_check_scale(result_tables))
    violations.extend(_check_unknown(result_tables))
    violations.extend(_check_missing(result_tables))
    violations.extend(_check_declared(result_tables, rules))
    return violations
