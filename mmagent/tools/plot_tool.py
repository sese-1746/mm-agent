"""出图工具：转发 pipeline.render 的确定性渲染。"""
from __future__ import annotations

from pathlib import Path

from ..pipeline import render as render_mod


def render(result_tables: dict, figure_specs: list[dict],
           out_dir: str | Path) -> dict:
    """按 figure_specs 渲染 PNG（dpi300）并写 figure_manifest.csv。

    返回 {"figures": [...], "warnings": [...], "manifest_path": str}。
    """
    manifest = render_mod.render_figures(result_tables, figure_specs, out_dir)
    return {
        "figures": manifest.figures,
        "warnings": manifest.warnings,
        "manifest_path": str(Path(out_dir) / "figure_manifest.csv"),
    }
