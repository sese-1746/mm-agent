"""FR-13 合规声明：生成 ai_disclosure.md 与 ai_disclosure.docx。

四字段（竞赛组委会要求）：
1. Agent 参与范围
2. 人改比例（由 Python 计算：人工事件数 / 总事件数；无数据时用传入值或默认 0.3）
3. 模型与提示词版本（从 artifacts/metric_obs 查询；查不到写占位）
4. 数据与素材来源声明
"""
from __future__ import annotations

from pathlib import Path

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

DEFAULT_HUMAN_EDIT_RATIO = 0.3  # 无事件数据时的保守默认值（人工复核兜底）


def _collect_llm_provenance(profile_id: str, version_id: int | None,
                            db_path: str | Path) -> dict:
    """从 artifacts / metric_obs 查模型与提示词版本；查不到返回占位。"""
    provenance = {"model_tags": [], "prompt_versions": []}
    try:
        conn = connect(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT model_tag FROM artifacts WHERE profile_id=? "
                "AND model_tag IS NOT NULL", (profile_id,)).fetchall()
            provenance["model_tags"] = sorted(r["model_tag"] for r in rows)
            rows = conn.execute(
                "SELECT DISTINCT prompt_version FROM metric_obs "
                "WHERE prompt_version IS NOT NULL").fetchall()
            provenance["prompt_versions"] = sorted(r["prompt_version"] for r in rows)
        finally:
            conn.close()
    except Exception:
        pass  # 库不存在或表缺失时用占位，不阻断声明生成
    return provenance


def _calc_human_edit_ratio(profile_id: str, db_path: str | Path) -> tuple[float, str]:
    """人改比例 = 人工 actor 事件数 / 总事件数；无事件时返回默认值并注明。"""
    try:
        conn = connect(db_path)
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
            human = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE actor LIKE 'human%'").fetchone()["c"]
        finally:
            conn.close()
        if total > 0:
            return round(human / total, 4), "按事件流水统计（人工事件/总事件）"
    except Exception:
        pass
    return DEFAULT_HUMAN_EDIT_RATIO, "无事件数据，采用保守默认值（待人工复核修订）"


def make_ai_disclosure(profile_id: str, version_id: int | None,
                       output_dir: str | Path,
                       human_edit_ratio: float | None = None,
                       db_path: str | Path = DEFAULT_DB_PATH,
                       data_sources: list[str] | None = None) -> Path:
    """生成 ai_disclosure.md / .docx，返回 md 路径。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if human_edit_ratio is None:
        ratio, ratio_note = _calc_human_edit_ratio(profile_id, db_path)
    else:
        ratio, ratio_note = human_edit_ratio, "由调用方人工核定"
    prov = _collect_llm_provenance(profile_id, version_id, db_path)
    model_tags = "、".join(prov["model_tags"]) or "（占位：尚无 LLM 调用记录）"
    prompt_versions = "、".join(prov["prompt_versions"]) or "（占位：尚无提示词版本记录）"
    sources = data_sources or ["竞赛官方附件（赛题发布数据集）"]
    ts = utcnow()

    md = f"""# AI 使用披露声明

- 档案：{profile_id}（version_id={version_id if version_id is not None else 'last'}）
- 生成时间（UTC）：{ts}

## 1. Agent 参与范围

本作品在准备过程中使用了本地部署的 AI 协作 Agent（mmagent）辅助完成以下环节：
附件数据体检、字段口径冲突提示、求解脚本起草、图表渲染与论文初稿组装。
所有 AI 产出均经队员人工审阅、修改与确认，最终内容由队员全权负责。

## 2. 人改比例

- 人改比例：{ratio:.1%}
- 口径说明：{ratio_note}。

## 3. 模型与提示词版本

- 模型标签：{model_tags}
- 提示词版本：{prompt_versions}
- 运行方式：全本地推理（Ollama，localhost:11434），无任何云端 API 调用。

## 4. 数据与素材来源声明

{chr(10).join(f'- {s}' for s in sources)}

全部数据与素材来源合法合规，未使用任何未经授权的外部数据。
"""
    md_path = output_dir / "ai_disclosure.md"
    md_path.write_text(md, encoding="utf-8")

    # docx 版（与 md 同内容，供随论文提交）
    from docx import Document

    doc = Document()
    doc.add_heading("AI 使用披露声明", level=1)
    doc.add_paragraph(f"档案：{profile_id}（version_id="
                      f"{version_id if version_id is not None else 'last'}）")
    doc.add_paragraph(f"生成时间（UTC）：{ts}")
    for title, body in [
        ("1. Agent 参与范围",
         "使用本地 AI 协作 Agent（mmagent）辅助：附件数据体检、字段口径冲突提示、"
         "求解脚本起草、图表渲染与论文初稿组装。所有 AI 产出均经队员人工审阅、"
         "修改与确认，最终内容由队员全权负责。"),
        ("2. 人改比例", f"人改比例：{ratio:.1%}。口径说明：{ratio_note}。"),
        ("3. 模型与提示词版本",
         f"模型标签：{model_tags}；提示词版本：{prompt_versions}；"
         "运行方式：全本地推理（Ollama，localhost:11434），无云端 API 调用。"),
        ("4. 数据与素材来源声明",
         "；".join(sources) + "。全部数据与素材来源合法合规。"),
    ]:
        doc.add_heading(title, level=2)
        doc.add_paragraph(body)
    doc.save(str(output_dir / "ai_disclosure.docx"))

    return md_path
