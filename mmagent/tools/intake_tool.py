"""体检工具：把 pipeline.intake 的确定性体检暴露为工具函数。"""
from __future__ import annotations

from pathlib import Path

from ..pipeline import intake


def run_intake(dataset_dir: str | Path, out_dir: str | Path) -> dict:
    """对数据集目录做附件体检，产出 4 件产物并返回汇总 dict。

    转发 pipeline.intake.run_intake_audit（确定性，不含 LLM）。
    """
    return intake.run_intake_audit(dataset_dir, out_dir)
