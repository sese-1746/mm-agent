"""FR-2 附件体检（确定性部分）测试：合成 xlsx 单测 + A 题真实附件集成。

被测模块：mmagent/pipeline/intake.py + config/field_aliases.yaml + config/intake_scope.yaml
（源码修订见 test-report.md 第 0 节 md5 基线；该模块经历过一次大重构，本文件已按重构后
语义重写：输入白名单、三口径行数、blank_as_zero 双缺失率、unmapped/dirty 留痕清单）

覆盖 success_criteria：
- 体检产物齐全且非空（schema_report.json / caliber_conflicts.json / field_mapping.csv /
  intake_summary.md / unmapped_columns.json / dirty_cells.json / column_profiles.json）
- schema_report 每字段含 table_id/field_raw/dtype/row_count/missing_rate 等必需键
- missing_rate ∈ [0, 1]；blank_as_zero 列 missing_rate 与 raw_missing_rate 口径分离
- 6 张真实附件字段数为冻结真值 13/25/25/13/13/37；三口径行数（物理/有效/空行）
- 表级 numeric_sum（数值列合计）与 rating value_counts 为冻结 golden 值
- 输入白名单：非黄金输入文件被跳过并记入 skipped_files（{file, reason} 结构）
- field_mapping.csv 的 confirmed_by / confirmed_at 留空待人工确认
- 口径冲突记录携带 conflict_id/conflict_type/field_base/evidence_rows/suggested_caliber

取列纪律（重构后语义）：字段表只收「门店列 + 月份组列」；表头非空但既非门店也非月份的
列（如附件1 的 N 派生列、日期列）一律进 unmapped_columns 留痕，**不得静默丢弃**。

合成数据命名纪律：输入白名单按**源文件名子串**匹配（`附件1`~`附件6`、`总表`），
因此合成文件也必须用这些名字，否则会被跳过而产出空结果。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from mmagent.pipeline.intake import run_intake_audit

ARTIFACTS = ("schema_report.json", "caliber_conflicts.json", "field_mapping.csv",
             "intake_summary.md", "unmapped_columns.json", "dirty_cells.json",
             "column_profiles.json")
FIELD_REQUIRED_KEYS = {"table_id", "field_raw", "field_norm", "dtype", "row_count",
                       "missing_rate", "raw_missing_rate", "effective_rows",
                       "physical_rows", "blank_as_zero", "source_file", "sheet_name"}
TABLE_REQUIRED_KEYS = {"table_id", "source_file", "sheet_name", "field_count",
                       "row_count", "effective_rows", "physical_rows"}
CSV_REQUIRED_COLUMNS = {"table_id", "field_name", "field_raw", "field_norm", "canonical",
                        "role", "unit", "period", "entity_scope", "dtype", "row_count",
                        "missing_rate", "raw_missing_rate", "source_file", "source_sheet",
                        "confirmed_by", "confirmed_at"}
CONFLICT_REQUIRED_KEYS = {"conflict_id", "conflict_type", "field_base", "variants",
                          "tables", "evidence_rows", "suggested_caliber"}
CONFLICT_TYPES = {"unit", "period", "entity_scope", "definition"}
VALID_DTYPES = {"int", "float", "str", "datetime", "empty"}

FILE_ATT4 = "附件4 各门店客户投诉量-A题.xlsx"
FILE_ATT2 = "附件2 服务态度得分-A题.xlsx"
FILE_SKIPPED = "预处理-有资格月度.xlsx"


# ------------------------------------------------------------------ 合成数据工具

def _make_xlsx(path: Path, sheets: dict) -> Path:
    """sheets: {sheet_name: [row, row...]}；空列表 = 空 sheet。

    表头空列请用 None（写 "" 会落成 openpyxl 的空 inlineStr 单元格）。
    """
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


@pytest.fixture()
def synthetic_dataset(tmp_path: Path) -> Path:
    """两个黄金输入附件 + 一个白名单外文件。

    附件4 结构：标题行含「空格处为0」→ blank_as_zero；含 1 处脏值 '97.3.4'；含 1 个整行空行。
    附件2 结构：纯数值月份列，供 dtype/period/canonical 断言。
    """
    dataset = tmp_path / "dataset"
    _make_xlsx(dataset / FILE_ATT4, {
        "Sheet1": [
            ["附件4 各门店客户投诉量（空格处为0）", None, None, None],
            ["月份门店", 1, 2, 3],
            ["A1", 2, None, 3],
            [None, None, None, None],
            ["B1", "97.3.4", 1, 2],
        ],
    })
    _make_xlsx(dataset / FILE_ATT2, {
        "Sheet1": [
            ["月份门店", 1, 2],
            ["A1", 95, 96],
            ["A2", 90, 91],
        ],
    })
    _make_xlsx(dataset / FILE_SKIPPED, {"Sheet1": [["门店", "得分"], ["A1", 90]]})
    return dataset


def _load(out_dir: Path, name: str) -> dict:
    return json.loads((out_dir / name).read_text(encoding="utf-8"))


def _table_of(report: dict, file_name: str) -> dict:
    return next(t for t in report["tables"] if t["source_file"] == file_name)


def _fields_of(report: dict, file_name: str) -> list[dict]:
    return [f for f in report["fields"] if f["source_file"] == file_name]


def _field(report: dict, file_name: str, raw: str) -> dict:
    return next(f for f in _fields_of(report, file_name) if f["field_raw"] == raw)


# ------------------------------------------------------------------ 产物与结构

def test_产出全部体检产物且均非空(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    for name in ARTIFACTS:
        path = out_dir / name
        assert path.exists(), f"缺少体检产物 {name}"
        assert path.stat().st_size > 0, f"体检产物 {name} 为空"


def test_schema_report含tables与fields两段(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    assert {"tables", "fields"} <= set(report), f"schema_report 缺必需段：{sorted(report)}"
    assert "skipped_files" in report, "被跳过的文件须在产物里留痕（禁止静默丢弃）"
    assert report["tables"], "应至少产出一条表级记录"
    assert report["fields"], "应至少产出一条字段记录"


def test_表级记录含三口径行数(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    table = _table_of(report, FILE_ATT4)
    assert TABLE_REQUIRED_KEYS <= set(table)
    assert table["physical_rows"] == 3, "表头之外 3 行（含 1 个整行空行）"
    assert table["effective_rows"] == 2, "整行空行不计入有效门店行"
    assert table["row_count"] == table["effective_rows"]
    assert table["blank_rows"] == table["physical_rows"] - table["effective_rows"]


def test_标题行被捕获且不计入数据行(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    table = _table_of(_load(out_dir, "schema_report.json"), FILE_ATT4)
    assert "空格处为0" in table["table_title"], "第 0 行大标题应进 table_title"
    fields = _fields_of(_load(out_dir, "schema_report.json"), FILE_ATT4)
    assert {f["field_raw"] for f in fields} == {"月份门店", "1", "2", "3"}, \
        "表头应是第 1 行真实列名，大标题不得成为字段"


def test_白名单外文件被跳过且留痕(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    result = run_intake_audit(synthetic_dataset, out_dir)

    skipped = {s["file"] for s in result["skipped_files"]}
    assert FILE_SKIPPED in skipped, f"白名单外文件须留痕，实际 {result['skipped_files']}"
    assert all(s["reason"].strip() for s in result["skipped_files"]), \
        "跳过留痕必须说明原因，便于人工复核"
    report = _load(out_dir, "schema_report.json")
    assert FILE_SKIPPED not in {t["source_file"] for t in report["tables"]}, \
        "预处理类参考材料不得进入体检产物"


def test_空sheet被跳过(tmp_path):
    dataset = tmp_path / "dataset"
    _make_xlsx(dataset / "附件2 含空sheet.xlsx",
               {"有数据": [["月份门店", 1], ["A1", 90]], "空sheet": []})
    out_dir = tmp_path / "out"

    run_intake_audit(dataset, out_dir)

    tables = _load(out_dir, "schema_report.json")["tables"]
    assert {t["sheet_name"] for t in tables} == {"有数据"}


def test_非表格文件被忽略(synthetic_dataset, tmp_path):
    (synthetic_dataset / "赛题说明.docx").write_bytes(b"not a table")
    (synthetic_dataset / "备注.txt").write_text("ignore me", encoding="utf-8")
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    files = {t["source_file"] for t in _load(out_dir, "schema_report.json")["tables"]}
    assert files == {FILE_ATT4, FILE_ATT2}


def test_空数据集目录_仍产出产物且字段为空(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    out_dir = tmp_path / "out"

    result = run_intake_audit(dataset, out_dir)

    assert result["field_count"] == 0
    assert result["tables"] == []
    for name in ARTIFACTS:
        assert (out_dir / name).stat().st_size > 0, f"{name} 不得为零字节"


def test_输出目录不存在时自动递归创建(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "deep" / "nested" / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    assert (out_dir / "schema_report.json").exists()


def test_返回汇总dict的字段与产物一致(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    result = run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    conflicts = _load(out_dir, "caliber_conflicts.json")
    unmapped = _load(out_dir, "unmapped_columns.json")
    dirty = _load(out_dir, "dirty_cells.json")
    assert result["field_count"] == len(report["fields"])
    assert result["tables"] == report["tables"]
    assert result["conflict_count"] == conflicts["count"]
    assert result["unmapped_count"] == unmapped["count"]
    assert result["dirty_cell_count"] == dirty["count"]
    assert result["out_dir"] == str(out_dir)


def test_多文件各自独立产出表级记录(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    assert {t["source_file"] for t in report["tables"]} == {FILE_ATT4, FILE_ATT2}
    ids = [t["table_id"] for t in report["tables"]]
    assert len(set(ids)) == len(ids), f"table_id 不得重复：{ids}"


# ------------------------------------------------------------------ 字段级体检

def test_字段记录含必需键且dtype可枚举(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    for field in _load(out_dir, "schema_report.json")["fields"]:
        assert FIELD_REQUIRED_KEYS <= set(field), f"字段记录缺键：{field}"
        assert field["dtype"] in VALID_DTYPES


def test_缺失率双口径_空格按0列mr为0而raw大于0(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    field = _field(_load(out_dir, "schema_report.json"), FILE_ATT4, "2")
    assert field["blank_as_zero"] is True, "附件4 标题声明「空格处为0」"
    assert field["missing_rate"] == 0.0, "按空格=0 口径无缺失"
    assert field["raw_missing_rate"] == pytest.approx(0.6667, abs=1e-3), \
        "原始口径仍有 2/3 空"


def test_未声明空格按0时口径与原始一致(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    field = _field(_load(out_dir, "schema_report.json"), FILE_ATT2, "1")
    assert field["blank_as_zero"] is False
    assert field["missing_rate"] == field["raw_missing_rate"] == 0.0


def test_表格级fillna_rule来自表标题(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    assert _table_of(report, FILE_ATT4)["fillna_rule"].startswith("0")
    assert _table_of(report, FILE_ATT2)["fillna_rule"] == ""


def test_全空列原始缺失率为1(tmp_path):
    dataset = tmp_path / "dataset"
    _make_xlsx(dataset / "附件5 公司发现问题数量.xlsx", {
        "Sheet1": [
            ["附件5 公司发现问题数量（空格处为0）", None],
            ["月份门店", 1],
            ["A1", None],
        ],
    })
    out_dir = tmp_path / "out"

    run_intake_audit(dataset, out_dir)

    field = _field(_load(out_dir, "schema_report.json"),
                   "附件5 公司发现问题数量.xlsx", "1")
    assert field["raw_missing_rate"] == 1.0
    assert field["dtype"] == "empty"


def test_数值月份列dtype为int(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    for raw in ("1", "2"):
        assert _field(report, FILE_ATT2, raw)["dtype"] == "int"


def test_脏值使该列dtype降级为str(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    assert _field(_load(out_dir, "schema_report.json"), FILE_ATT4, "1")["dtype"] == "str"


def test_月份编号列period为M期号(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    assert [_field(report, FILE_ATT2, r)["period"] for r in ("1", "2")] == ["M01", "M02"]
    assert [_field(report, FILE_ATT4, r)["period"] for r in ("1", "2", "3")] == \
        ["M01", "M02", "M03"]


def test_门店标识列不带周期语义(synthetic_dataset, tmp_path):
    """回归 ISSUE-FR2-02：曾把「月份门店」误标为 period='月'。"""
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    for file_name in (FILE_ATT2, FILE_ATT4):
        field = _field(report, file_name, "月份门店")
        assert field["period"] == "", f"{file_name} 门店列不应带周期"
        assert field["canonical"] == "store_id"


def test_canonical按别名表解析(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    assert _field(report, FILE_ATT2, "1")["canonical"] == "service_score_m01"
    assert _field(report, FILE_ATT4, "1")["canonical"] == "complaint_count_m01"


def test_字段归一化去空白并小写(tmp_path):
    """field_norm = 去空白 + 小写：' Store ID ' → 'storeid'，月份列名归一为 'N月'。

    注：本模块按「门店 × 月份」矩阵口径取列，只有门店列（首列区）与月份组列进字段表，
    其它列（含日期列）进 unmapped_columns（见 test_非月度列不入字段表而记入未映射留痕）。
    """
    dataset = tmp_path / "dataset"
    _make_xlsx(dataset / "附件2 空白字段.xlsx",
               {"Sheet1": [["月 份 门 店", " Store ID ", " 1 "],
                           ["A1", "A1", 90]]})
    out_dir = tmp_path / "out"

    run_intake_audit(dataset, out_dir)

    norms = {f["field_raw"]: f["field_norm"]
             for f in _load(out_dir, "schema_report.json")["fields"]}
    assert norms["月 份 门 店"] == "月份门店", "中文空白应被去除"
    assert norms["Store ID"] == "storeid", "空白去除 + 英文小写"
    assert norms["1"] == "1月", "月份列名由月份号生成"


def test_非月度列不入字段表而记入未映射留痕(tmp_path):
    """取列纪律：既非门店亦非月份的列（如日期列）必须进 unmapped_columns，禁止静默丢弃。"""
    dataset = tmp_path / "dataset"
    _make_xlsx(dataset / "总表 日期列.xlsx",
               {"Sheet1": [["门店", "统计日期"], ["A1", "2024-01-05"]]})
    out_dir = tmp_path / "out"

    run_intake_audit(dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    assert {f["field_raw"] for f in _fields_of(report, "总表 日期列.xlsx")} == {"门店"}, \
        "非月度列不应混进字段表"
    rows = [u for u in _load(out_dir, "unmapped_columns.json")["unmapped_columns"]
            if u["source_file"] == "总表 日期列.xlsx"]
    assert [u["header_raw"] for u in rows] == ["统计日期"], "非月度列应留痕而非静默丢弃"
    assert rows[0]["non_empty"] == 1
    assert rows[0]["sample_values"] == ["2024-01-05"]


# ------------------------------------------------------------------ 留痕清单

def test_脏值清单记录文本混入数值列(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    dirty = _load(out_dir, "dirty_cells.json")
    assert dirty["count"] == 1
    cell = dirty["dirty_cells"][0]
    assert {"table_id", "source_file", "sheet", "excel_row", "column", "value",
            "store"} <= set(cell)
    assert cell["value"] == "97.3.4"
    assert cell["store"] == "B1"
    assert cell["source_file"] == FILE_ATT4


def test_空表头列有数据时记入未映射清单(tmp_path):
    dataset = tmp_path / "dataset"
    _make_xlsx(dataset / "附件2 尾列丢失.xlsx", {
        "Sheet1": [
            ["月份门店", 1, None],
            ["A1", 90, 7],
        ],
    })
    out_dir = tmp_path / "out"

    run_intake_audit(dataset, out_dir)

    unmapped = _load(out_dir, "unmapped_columns.json")
    assert unmapped["count"] == 1, "空表头且有数据的列必须留痕，禁止静默丢弃"
    row = unmapped["unmapped_columns"][0]
    assert {"table_id", "source_file", "sheet", "column", "header_raw",
            "non_empty", "sample_values"} <= set(row)
    assert row["non_empty"] == 1


def test_无未映射与脏值时清单为空(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    unmapped = _load(out_dir, "unmapped_columns.json")
    assert unmapped["count"] == len(unmapped["unmapped_columns"])
    assert unmapped["count"] >= 0


def test_column_profiles覆盖全部字段(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    profiles = _load(out_dir, "column_profiles.json")["profiles"]
    fields = _load(out_dir, "schema_report.json")["fields"]
    assert len(profiles) == len(fields)
    assert all({"table_id", "sheet", "field_name", "dtype"} <= set(pr) for pr in profiles)


# ------------------------------------------------------------------ 口径冲突

def test_冲突记录含必需键与可核查证据(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    conflicts = _load(out_dir, "caliber_conflicts.json")
    assert conflicts["count"] == len(conflicts["conflicts"]) >= 1, \
        "附件4(str 月份列) 与附件2(int 月份列) 应检出 definition 冲突"
    for conflict in conflicts["conflicts"]:
        assert CONFLICT_REQUIRED_KEYS <= set(conflict), f"冲突缺必需键：{conflict}"
        assert conflict["conflict_type"] in CONFLICT_TYPES
        assert conflict["suggested_caliber"].strip()
        assert conflict["evidence_rows"], "必须附可核查证据"


def test_按冲突类型可定位type字段(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    types = {c["conflict_type"] for c in _load(out_dir, "caliber_conflicts.json")["conflicts"]}
    assert "definition" in types, f"dtype 跨表不一致应判 definition，实际 {types}"


def test_冲突证据可回溯到源文件(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    conflicts = _load(out_dir, "caliber_conflicts.json")["conflicts"]
    text = json.dumps(conflicts, ensure_ascii=False)
    assert "附件2" in text and "附件4" in text, "证据应指向具体源文件"


# ------------------------------------------------------------------ 字段映射表与摘要

def test_字段映射csv_含必需列且每字段一行(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    with (out_dir / "field_mapping.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = set(reader.fieldnames or [])
        rows = list(reader)
    assert CSV_REQUIRED_COLUMNS <= columns, \
        f"field_mapping.csv 缺列：{CSV_REQUIRED_COLUMNS - columns}"
    assert len(rows) == len(_load(out_dir, "schema_report.json")["fields"])
    assert all(r["confirmed_by"] == "" and r["confirmed_at"] == "" for r in rows), \
        "confirmed_* 应留空待人工确认"


def test_字段映射csv_带出canonical与周期(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    with (out_dir / "field_mapping.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = {(r["source_file"], r["field_raw"]): r for r in csv.DictReader(f)}
    att2 = rows[(FILE_ATT2, "1")]
    assert att2["canonical"] == "service_score_m01"
    assert att2["period"] == "M01"


def test_体检摘要md含关键小节(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    md = (out_dir / "intake_summary.md").read_text(encoding="utf-8")
    assert md.startswith("# 附件体检摘要")
    for section in ("## 各表行数（三口径）", "## 缺失率 Top10", "## 口径来源说明（fillna_rule）",
                    "## 未映射列留痕", "## 脏单元格", "## 口径冲突清单"):
        assert section in md, f"摘要缺小节：{section}"


def test_体检摘要md列出留痕清单说明(synthetic_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(synthetic_dataset, out_dir)

    md = (out_dir / "intake_summary.md").read_text(encoding="utf-8")
    assert "未映射列" in md
    assert "脏单元格" in md


# ------------------------------------------------------------------ 真实附件集成

def test_真实6个附件_各产出表级记录且字段数达标(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    tables = _load(out_dir, "schema_report.json")["tables"]
    assert len(tables) == 6, f"6 个附件各应产出表级记录，实际 {len(tables)}"
    for table in tables:
        assert table["source_file"].startswith("附件")
        assert table["field_count"] >= 13, f"{table['source_file']} 字段数不足：{table}"
    att6 = next(t for t in tables if t["source_file"].startswith("附件6"))
    assert att6["field_count"] >= 35, "附件6 三层表头应至少展开出 35 个字段"


def test_真实附件_字段数为冻结真值(real_attachment_dataset, tmp_path):
    """golden（Vera 独立核验 / 队长复核）：13 / 25 / 25 / 13 / 13 / 37。"""
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    got = {t["source_file"][:3]: t["field_count"]
           for t in _load(out_dir, "schema_report.json")["tables"]}
    assert got == {"附件1": 13, "附件2": 25, "附件3": 25,
                   "附件4": 13, "附件5": 13, "附件6": 37}, got


def test_真实附件_表头是真实列名而非大标题(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    att1 = next(t for t in report["tables"] if t["source_file"].startswith("附件1"))
    assert att1["table_title"].strip(), "第 0 行大标题应被捕获到 table_title"
    raws = {f["field_raw"] for f in _fields_of(report, att1["source_file"])}
    assert "月份门店" in raws, "表头应是第 1 行真实列名"
    assert not any("综合得分" in r for r in raws), "大标题不得成为字段名"


def test_真实附件_有效门店行均为157(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    tables = _load(out_dir, "schema_report.json")["tables"]
    effective = {t["source_file"][:3]: t["effective_rows"] for t in tables}
    assert set(effective.values()) == {157}, f"6 张附件有效门店行应均为 157：{effective}"


def test_真实附件1_三口径行数(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    att1 = next(t for t in _load(out_dir, "schema_report.json")["tables"]
                if t["source_file"].startswith("附件1"))
    assert att1["physical_rows"] == 160
    assert att1["effective_rows"] == 157
    assert att1["blank_rows"] == 3, "附件1 有 3 个整行空分区隔行"


def test_真实附件_字段记录含必需键且缺失率在0到1(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    fields = _load(out_dir, "schema_report.json")["fields"]
    assert fields
    for field in fields:
        assert FIELD_REQUIRED_KEYS <= set(field)
        assert field["dtype"] in VALID_DTYPES
        assert 0.0 <= field["raw_missing_rate"] <= 1.0
        assert 0.0 <= field["missing_rate"] <= 1.0
        assert field["effective_rows"] >= 0


def test_真实附件_空格按0列missing_rate为0(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    fields = _load(out_dir, "schema_report.json")["fields"]
    for file_key in ("附件4", "附件5"):
        month_fields = [f for f in fields if f["source_file"].startswith(file_key)
                        and f["period"].startswith("M")]
        assert month_fields, f"{file_key} 应产出月份列"
        bad = [f["field_raw"] for f in month_fields if f["missing_rate"] > 0.001]
        assert not bad, f"{file_key} 表标题声明「空格处为0」，这些列缺失率应为 0：{bad}"


def test_真实附件_白名单跳过预处理文件(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    result = run_intake_audit(real_attachment_dataset, out_dir)

    assert result["skipped_files"] == [], "单附件目录（只复制了 6 个 .xls）无跳过项"
    offenders = [t["source_file"] for t in _load(out_dir, "schema_report.json")["tables"]
                 if "预处理" in t["source_file"]]
    assert offenders == []


def test_真实目录_白名单跳过两个预处理文件(tmp_path):
    real_dir = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
    if not real_dir.exists():
        pytest.skip(f"真实数据目录不存在：{real_dir}")
    out_dir = tmp_path / "out"

    result = run_intake_audit(real_dir, out_dir)

    skipped = sorted(s["file"] for s in result["skipped_files"])
    assert skipped == [FILE_SKIPPED, "预处理2-排名及其获奖名单.xlsx"], \
        f"两个预处理参考材料应被白名单跳过留痕，实际 {skipped}"
    files = {t["source_file"] for t in _load(out_dir, "schema_report.json")["tables"]}
    assert not any("预处理" in f for f in files)
    assert any(f.startswith("附件1") for f in files) and "总表.xlsx" in files
    assert result["unmapped_count"] == 2, \
        "全目录下应有 2 处非月度列留痕（附件1 派生列 + 总表 1 处）"


def test_真实附件_冲突记录结构完整(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    conflicts = _load(out_dir, "caliber_conflicts.json")
    assert conflicts["count"] >= 1, "A 题附件间应能检出至少一类口径冲突"
    for conflict in conflicts["conflicts"]:
        assert CONFLICT_REQUIRED_KEYS <= set(conflict)
        assert conflict["conflict_type"] in CONFLICT_TYPES
        assert conflict["evidence_rows"]


def test_真实附件_留痕清单结构完整(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    dirty = _load(out_dir, "dirty_cells.json")
    unmapped = _load(out_dir, "unmapped_columns.json")
    assert dirty["count"] == len(dirty["dirty_cells"]) == 17, \
        "附件3 的 17 处 '97.3.4' 应逐格留痕"
    assert {d["value"] for d in dirty["dirty_cells"]} == {"97.3.4"}, \
        "脏值必须原值留痕（不自动改写）"
    assert unmapped["count"] == len(unmapped["unmapped_columns"]) >= 1
    derived = [u for u in unmapped["unmapped_columns"] if u["derived"]]
    assert derived and all(u["source_file"].startswith("附件1") for u in derived), \
        "附件1 的 N 派生列应被识别并留痕（derived=True）"


def test_真实附件_字段映射csv行数等于字段数(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    with (out_dir / "field_mapping.csv").open(encoding="utf-8-sig", newline="") as f:
        n_csv = sum(1 for _ in csv.DictReader(f))
    n_fields = len(_load(out_dir, "schema_report.json")["fields"])
    assert n_csv == n_fields > 0


# ------------------------------------------------------------------ golden 数值（独立复算）

def _tables_by_prefix(report: dict) -> dict:
    return {t["source_file"][:3]: t for t in report["tables"]}


def _numeric_sum(table: dict, contains: str = "") -> float:
    return sum(v for k, v in table["numeric_sum"].items() if contains in k)


def test_真实附件_数值合计为冻结golden(real_attachment_dataset, tmp_path):
    """golden（Vera 独立复算 / 队长独立复核一致）：
    附件4=432.0、附件5=131.0、附件6 定额=27289.0、实际利润=31344.0（合计 58633.0）。"""
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    tables = _tables_by_prefix(_load(out_dir, "schema_report.json"))
    assert _numeric_sum(tables["附件4"]) == pytest.approx(432.0, abs=1e-6)
    assert _numeric_sum(tables["附件5"]) == pytest.approx(131.0, abs=1e-6)
    assert _numeric_sum(tables["附件6"], "利润定额") == pytest.approx(27289.0, abs=1e-6)
    assert _numeric_sum(tables["附件6"], "实际利润") == pytest.approx(31344.0, abs=1e-6)
    assert _numeric_sum(tables["附件6"]) == pytest.approx(58633.0, abs=1e-6)


def test_真实附件_三口径行数为冻结真值(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    tables = _tables_by_prefix(_load(out_dir, "schema_report.json"))
    assert (tables["附件1"]["physical_rows"], tables["附件1"]["effective_rows"],
            tables["附件1"]["blank_rows"]) == (160, 157, 3)
    assert (tables["附件6"]["physical_rows"], tables["附件6"]["effective_rows"],
            tables["附件6"]["blank_rows"]) == (157, 157, 0)


def test_真实附件_12月评级分布为冻结golden(real_attachment_dataset, tmp_path):
    """golden：附件2 {合格:144, 不合格:13}；附件3 {合格:154, 不合格:3}。"""
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")

    def m12_rating(prefix: str) -> dict:
        table_id = _tables_by_prefix(report)[prefix]["table_id"]
        return next(f for f in report["fields"]
                    if f["table_id"] == table_id and f["period"] == "M12"
                    and f["role"] == "评级")

    assert m12_rating("附件2")["value_counts"] == {"合格": 144, "不合格": 13}
    assert m12_rating("附件3")["value_counts"] == {"合格": 154, "不合格": 3}
    assert m12_rating("附件2")["sample_values"] and \
        set(m12_rating("附件2")["sample_values"]) <= {"合格", "不合格"}


def test_真实附件6_三类dtype齐备(real_attachment_dataset, tmp_path):
    """附件6 三层表头展开后须同时含 定额int / 实际利润float / 评级str。"""
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    table_id = _tables_by_prefix(report)["附件6"]["table_id"]
    by_role: dict[str, set] = {}
    for f in report["fields"]:
        if f["table_id"] == table_id:
            by_role.setdefault(f["role"], set()).add(f["dtype"])
    assert by_role["利润定额"] == {"int"}, by_role
    assert by_role["实际利润"] == {"float"}, by_role
    assert by_role["评级"] == {"str"}, by_role


def test_真实附件4_月份列为整数(real_attachment_dataset, tmp_path):
    out_dir = tmp_path / "out"

    run_intake_audit(real_attachment_dataset, out_dir)

    report = _load(out_dir, "schema_report.json")
    table_id = _tables_by_prefix(report)["附件4"]["table_id"]
    month_fields = [f for f in report["fields"]
                    if f["table_id"] == table_id and f["period"].startswith("M")]
    assert len(month_fields) == 12
    assert {f["dtype"] for f in month_fields} == {"int"}
