"""数据核查（sanity）测试：内置关键词规则 + 自定义规则 + 边界。

被测模块：mmagent/checks/sanity.py（FR-5 结果理智性检查）
覆盖 success_criteria：
- 得分 0~100 越界、占比 >1、利润 <0 各命中 violation 且 blocking=True
- 主键重复（unique 规则）命中 violation
- 干净数据 passed=True、violations 为空、blocking=False
- 自定义 range/not_null/row_count_match 规则生效；blocking=False 时阻断标志为假
"""
from __future__ import annotations

import pandas as pd
import pytest

from mmagent.checks.sanity import run_sanity_checks

VIOLATION_KEYS = {"rule", "table", "column", "rows", "message", "blocking"}


def _rules(violations, rule=None):
    return [v for v in violations if rule is None or v["rule"] == rule]


# ------------------------------------------------------------------ 内置规则

def test_得分越界被拦截且blocking():
    tables = {"scores": pd.DataFrame({"门店": ["A", "B", "C"], "得分": [50, 120, -3]})}

    report = run_sanity_checks(tables)

    assert report.passed is False
    assert report.blocking is True
    hits = _rules(report.violations, "builtin_range")
    assert len(hits) == 1
    assert hits[0]["column"] == "得分"
    assert hits[0]["rows"] == [1, 2], "只列出真正越界的行号"
    assert hits[0]["blocking"] is True


def test_占比大于1被拦截():
    tables = {"share": pd.DataFrame({"门店": ["A", "B"], "市场占比": [0.3, 1.5]})}

    report = run_sanity_checks(tables)

    assert report.passed is False
    hit = _rules(report.violations, "builtin_range")[0]
    assert hit["column"] == "市场占比"
    assert hit["rows"] == [1]
    assert "期望 0~1" in hit["message"]


def test_利润为负被拦截():
    tables = {"profit": pd.DataFrame({"门店": ["A", "B"], "月利润(万元)": [-5.0, 10.0]})}

    report = run_sanity_checks(tables)

    assert report.passed is False
    assert report.blocking is True
    assert _rules(report.violations, "builtin_range")[0]["rows"] == [0]


def test_内置关键词匹配大小写不敏感():
    tables = {"t": pd.DataFrame({"Score": [90, 101]})}

    report = run_sanity_checks(tables)

    assert _rules(report.violations, "builtin_range"), "Score 应命中 score 关键词"


def test_内置关键词命中英文列名ratio():
    tables = {"t": pd.DataFrame({"ratio": [0.1, 2.0]})}

    report = run_sanity_checks(tables)

    assert _rules(report.violations, "builtin_range")[0]["rows"] == [1]


def test_非数值列不误报():
    tables = {"t": pd.DataFrame({"得分等级": ["优秀", "良好"]})}

    report = run_sanity_checks(tables)

    assert report.violations == []
    assert report.passed is True


def test_干净数据通过():
    tables = {"scores": pd.DataFrame({"门店": ["A", "B"], "得分": [80, 100]})}

    report = run_sanity_checks(tables)

    assert report.passed is True
    assert report.blocking is False
    assert report.violations == []


def test_空结果表集合通过():
    report = run_sanity_checks({})

    assert report.passed is True
    assert report.violations == []


def test_violation结构含约定键():
    tables = {"t": pd.DataFrame({"得分": [200]})}

    report = run_sanity_checks(tables)

    assert VIOLATION_KEYS <= set(report.violations[0])


def test_violation最多保留20个行号():
    tables = {"t": pd.DataFrame({"得分": [200] * 25})}

    report = run_sanity_checks(tables)

    hit = report.violations[0]
    assert len(hit["rows"]) == 20, "行号列表应截断到 20，避免报告膨胀"
    assert "25" in hit["message"], "命中行数总量仍应体现在 message 中"


def test_多个表多个列同时越界():
    tables = {
        "scores": pd.DataFrame({"得分": [200]}),
        "share": pd.DataFrame({"占比": [1.2]}),
    }

    report = run_sanity_checks(tables)

    assert {v["table"] for v in report.violations} == {"scores", "share"}
    assert len(report.violations) == 2


# ------------------------------------------------------------------ 自定义规则

def test_主键重复命中unique规则():
    tables = {"rank": pd.DataFrame({"门店ID": ["A1", "A1", "A2"]})}
    rules = [{"name": "unique", "table": "rank", "column": "门店ID"}]

    report = run_sanity_checks(tables, rules)

    assert report.passed is False
    assert report.blocking is True
    hit = _rules(report.violations, "unique")[0]
    assert hit["rows"] == [0, 1], "重复组内所有行都应列出"
    assert "重复" in hit["message"]


def test_主键唯一时不报violation():
    tables = {"rank": pd.DataFrame({"门店ID": ["A1", "A2", "A3"]})}
    rules = [{"name": "unique", "table": "rank", "column": "门店ID"}]

    report = run_sanity_checks(tables, rules)

    assert report.passed is True


def test_自定义range规则生效():
    tables = {"t": pd.DataFrame({"满意度": [70, 95]})}
    rules = [{"name": "range", "table": "t", "column": "满意度", "min": 80, "max": 100}]

    report = run_sanity_checks(tables, rules)

    hit = _rules(report.violations, "range")[0]
    assert hit["rows"] == [0]
    assert hit["rule"] == "range"


def test_自定义range仅设上限():
    tables = {"t": pd.DataFrame({"投诉量": [1, 99]})}
    rules = [{"name": "range", "table": "t", "column": "投诉量", "max": 50}]

    report = run_sanity_checks(tables, rules)

    assert _rules(report.violations, "range")[0]["rows"] == [1]


def test_not_null规则命中空值():
    tables = {"t": pd.DataFrame({"门店": ["A", None, "C"]})}
    rules = [{"name": "not_null", "table": "t", "column": "门店"}]

    report = run_sanity_checks(tables, rules)

    hit = _rules(report.violations, "not_null")[0]
    assert hit["rows"] == [1]
    assert "空值" in hit["message"]


def test_row_count_match规则命中行数不一致():
    tables = {"a": pd.DataFrame({"x": [1, 2, 3]}), "b": pd.DataFrame({"x": [1, 2]})}
    rules = [{"name": "row_count_match", "tables": ["a", "b"]}]

    report = run_sanity_checks(tables, rules)

    hit = _rules(report.violations, "row_count_match")[0]
    assert hit["blocking"] is True
    assert "不一致" in hit["message"]


def test_row_count_match行数一致时不报():
    tables = {"a": pd.DataFrame({"x": [1, 2]}), "b": pd.DataFrame({"x": [3, 4]})}
    rules = [{"name": "row_count_match", "tables": ["a", "b"]}]

    report = run_sanity_checks(tables, rules)

    assert report.passed is True


def test_非阻断规则_违规但blocking为假():
    tables = {"t": pd.DataFrame({"满意度": [70]})}
    rules = [{"name": "range", "table": "t", "column": "满意度",
              "min": 80, "max": 100, "blocking": False}]

    report = run_sanity_checks(tables, rules)

    assert report.passed is False, "违规仍要如实登记"
    assert report.blocking is False, "非阻断规则不应阻断流程"
    assert report.violations[0]["blocking"] is False


def test_阻断与非阻断并存时blocking为真():
    tables = {"t": pd.DataFrame({"满意度": [70], "得分": [200]})}
    rules = [{"name": "range", "table": "t", "column": "满意度",
              "min": 80, "max": 100, "blocking": False}]

    report = run_sanity_checks(tables, rules)

    assert report.blocking is True
    assert len(report.violations) == 2


def test_规则指向不存在的表或列时静默跳过():
    tables = {"t": pd.DataFrame({"得分": [10]})}
    rules = [{"name": "unique", "table": "missing", "column": "门店ID"},
             {"name": "not_null", "table": "t", "column": "不存在的列"}]

    report = run_sanity_checks(tables, rules)

    assert report.passed is True, "规则无法命中目标时不应伪造 violation"


# ------------------------------------------------------------------ 边界与已知偏差

def test_缺失值不应被判为越界值():
    """回归 ISSUE-SANITY-01：_check_range 曾用 col.notna() 参与掩码，把 NaN 并入越界行号。"""
    tables = {"scores": pd.DataFrame({"得分": [50.0, float("nan"), 120.0]})}

    report = run_sanity_checks(tables)

    hit = _rules(report.violations, "builtin_range")[0]
    assert 1 not in hit["rows"], "NaN 行不应出现在越界行号里"
    assert hit["rows"] == [2]


def test_全空数值列不触发越界_缺失由not_null规则单独管():
    """回归 ISSUE-SANITY-01 的另一面：整列全空曾被判为「全部越界」。

    缺失去向应由 not_null 规则负责，range 规则只对非空值判定。
    """
    tables = {"scores": pd.DataFrame({"得分": [float("nan"), float("nan")]})}

    report = run_sanity_checks(tables)

    assert report.passed is True, "全空列不应产生越界违规"
    assert _rules(report.violations, "builtin_range") == []

    with_notnull = run_sanity_checks(
        tables, rules=[{"name": "not_null", "table": "scores", "column": "得分"}])
    assert with_notnull.passed is False, "缺失应由 not_null 规则单独报出"
    assert _rules(with_notnull.violations, "not_null")[0]["rows"] == [0, 1]


def test_布尔列不触发得分规则():
    tables = {"t": pd.DataFrame({"是否达标": [True, False]})}

    report = run_sanity_checks(tables)

    assert report.passed is True
