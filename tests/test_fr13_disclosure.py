"""FR-13 合规声明测试：ai_disclosure.md + ai_disclosure.docx 双产物。

被测模块：mmagent/compliance/disclosure.py
覆盖 success_criteria：
- make_ai_disclosure 生成 md + docx 双文件且非空
- md 含「Agent 参与范围」「人改比例」「模型与提示词版本」「数据与素材来源声明」四字段
- 人改比例：显式传入 > 事件流水统计 > 无数据时保守默认值（0.3）
- 模型标签/提示词版本可从 artifacts/metric_obs 溯源，查不到写占位
- 数据来源可自定义；输出目录自动创建
"""
from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from mmagent.compliance.disclosure import DEFAULT_HUMAN_EDIT_RATIO, make_ai_disclosure
from mmagent.store.db import connect, init_db, utcnow

PROFILE = "profile-A"
SECTIONS = ("1. Agent 参与范围", "2. 人改比例", "3. 模型与提示词版本",
            "4. 数据与素材来源声明")


def _empty_db_path(tmp_path: Path) -> Path:
    """指向一个从未初始化的库路径（无表 → 走占位/默认分支）。"""
    return tmp_path / "nodata" / "mm.db"


def _db_with_events(tmp_path: Path, actors: list[str]) -> Path:
    db_path = tmp_path / "with_events.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        for actor in actors:
            conn.execute(
                "INSERT INTO events (ts, event_type, actor, payload, trace_id)"
                " VALUES (?,?,?,?,?)", (utcnow(), "test.event", actor, "{}", "t"))
        conn.commit()
    finally:
        conn.close()
    return db_path


def _db_with_provenance(tmp_path: Path, model_tag: str, prompt_version: str) -> Path:
    db_path = tmp_path / "provenance.db"
    init_db(db_path)
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO artifacts (ts, kind, path, profile_id, version_id, producer,"
            " prompt_version, model_tag, input_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (utcnow(), "llm_output", "out/x.json", PROFILE, 1, "planner",
             prompt_version, model_tag, "hash"))
        conn.execute(
            "INSERT INTO metric_obs (ts, metric, value, unit, run_id, prompt_version,"
            " model_tag) VALUES (?,?,?,?,?,?,?)",
            (utcnow(), "llm_latency", 1.2, "s", "run-1", prompt_version, model_tag))
        conn.commit()
    finally:
        conn.close()
    return db_path


def _read_md(out_dir: Path) -> str:
    return (out_dir / "ai_disclosure.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ 产物

def test_生成md与docx双文件且非空(tmp_path):
    out_dir = tmp_path / "out"

    md_path = make_ai_disclosure(PROFILE, 1, out_dir,
                                 human_edit_ratio=0.2,
                                 db_path=_empty_db_path(tmp_path))

    assert isinstance(md_path, Path)
    assert md_path == out_dir / "ai_disclosure.md"
    for name in ("ai_disclosure.md", "ai_disclosure.docx"):
        path = out_dir / name
        assert path.exists(), f"缺少披露产物 {name}"
        assert path.stat().st_size > 0, f"{name} 不得为空"


def test_md含四个关键字段小节(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, None, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    for section in SECTIONS:
        assert section in md, f"md 缺少小节：{section}"
    assert md.startswith("# AI 使用披露声明")
    assert "Agent 参与范围" in md
    assert "模型" in md
    assert "来源" in md


def test_md记录档案与版本号(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 7, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    assert PROFILE in md
    assert "version_id=7" in md


def test_版本号为空时写last(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, None, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    assert "version_id=last" in _read_md(out_dir)


def test_输出目录不存在时自动递归创建(tmp_path):
    out_dir = tmp_path / "deep" / "nested" / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    assert (out_dir / "ai_disclosure.docx").exists()


def test_重复生成时覆盖且内容自洽(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))
    make_ai_disclosure(PROFILE, 2, out_dir, human_edit_ratio=0.4,
                       db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    assert "version_id=2" in md
    assert "40.0%" in md


# ------------------------------------------------------------------ 人改比例

def test_显式人改比例写入并注明口径(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.42,
                       db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    assert "42.0%" in md
    assert "由调用方人工核定" in md


def test_人改比例由事件流水统计(tmp_path):
    out_dir = tmp_path / "out"
    db_path = _db_with_events(tmp_path, ["human:队长", "planner", "coder", "planner"])

    make_ai_disclosure(PROFILE, 1, out_dir, db_path=db_path)

    md = _read_md(out_dir)
    assert "25.0%" in md, "1 条人工事件 / 4 条总事件 = 25%"
    assert "按事件流水统计" in md


def test_无事件数据时使用保守默认值(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    assert f"{DEFAULT_HUMAN_EDIT_RATIO:.1%}" in md
    assert "保守默认值" in md


def test_零比例也如实写入(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.0,
                       db_path=_empty_db_path(tmp_path))

    assert "0.0%" in _read_md(out_dir)


# ------------------------------------------------------------------ 模型与提示词溯源

def test_模型标签与提示词版本来自溯源表(tmp_path):
    out_dir = tmp_path / "out"
    db_path = _db_with_provenance(tmp_path, "ollama/qwen3.5:latest", "planner@1.0.0")

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2, db_path=db_path)

    md = _read_md(out_dir)
    assert "ollama/qwen3.5:latest" in md
    assert "planner@1.0.0" in md
    assert "Ollama" in md, "应声明全本地推理方式"


def test_无可溯源数据时写占位(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    assert "占位" in md
    assert "尚无 LLM 调用记录" in md


# ------------------------------------------------------------------ 数据来源

def test_自定义数据来源被列出(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path),
                       data_sources=["附件1 各门店客户满意度综合得分-A题.xls",
                                     "赛题原文 A题.docx"])

    md = _read_md(out_dir)
    assert "附件1 各门店客户满意度综合得分-A题.xls" in md
    assert "赛题原文 A题.docx" in md


def test_未指定数据来源时用默认值(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    assert "竞赛官方附件" in _read_md(out_dir)


def test_声明含合规承诺(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path))

    md = _read_md(out_dir)
    assert "合法合规" in md
    assert "全权负责" in md


# ------------------------------------------------------------------ docx 侧

def test_docx可读回且含四个小节(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 3, out_dir, human_edit_ratio=0.25,
                       db_path=_empty_db_path(tmp_path))

    doc = Document(str(out_dir / "ai_disclosure.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    for section in SECTIONS:
        assert section in text, f"docx 缺少小节：{section}"
    assert "AI 使用披露声明" in text
    assert "version_id=3" in text


def test_docx与md人改比例一致(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.42,
                       db_path=_empty_db_path(tmp_path))

    doc = Document(str(out_dir / "ai_disclosure.docx"))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "42.0%" in text
    assert "42.0%" in _read_md(out_dir)


def test_docx含自定义数据来源(tmp_path):
    out_dir = tmp_path / "out"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=0.2,
                       db_path=_empty_db_path(tmp_path),
                       data_sources=["附件6 各门店月利润定额及实际利润值-A题.xls"])

    doc = Document(str(out_dir / "ai_disclosure.docx"))
    assert "附件6" in "\n".join(p.text for p in doc.paragraphs)


@pytest.mark.parametrize("ratio,expected", [(0.0, "0.0%"), (0.3, "30.0%"),
                                            (1.0, "100.0%")])
def test_人改比例格式化为百分数(tmp_path, ratio, expected):
    out_dir = tmp_path / f"out{ratio}"

    make_ai_disclosure(PROFILE, 1, out_dir, human_edit_ratio=ratio,
                       db_path=_empty_db_path(tmp_path))

    assert expected in _read_md(out_dir)
