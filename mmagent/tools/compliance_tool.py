"""合规工具：转发 compliance.disclosure 的 AI 使用披露生成。"""
from __future__ import annotations

from pathlib import Path

from ..compliance import disclosure


def make_disclosure(profile_id: str, version_id: int | None,
                    output_dir: str | Path,
                    human_edit_ratio: float | None = None,
                    db_path: str | Path = disclosure.DEFAULT_DB_PATH,
                    data_sources: list[str] | None = None) -> Path:
    """生成 ai_disclosure.md 与 .docx，返回 md 路径（转发 compliance.disclosure）。"""
    return disclosure.make_ai_disclosure(
        profile_id, version_id, output_dir,
        human_edit_ratio=human_edit_ratio, db_path=db_path,
        data_sources=data_sources)
