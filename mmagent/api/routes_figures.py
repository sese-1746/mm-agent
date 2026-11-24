"""出图路由（FR-6）：**复用** `tools/plot_tool.render`（→ `pipeline.render.render_figures`）。

路由层**不**另写渲染逻辑；只做「解析 `result_tables` 字符串 → 读表 → 调 render → 组响应」。

`result_tables` 表 id 规则：写成 `name=path` 取 `name`，否则取 `Path(path).stem`（与前端 §4 一致）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..tools import plot_tool

router = APIRouter(prefix="/api/v1", tags=["figures"])


class RenderRequest(BaseModel):
    """出图请求体。"""
    result_tables: list[str]
    figure_specs: list[dict]
    out_dir: str


def _split_table_item(item: str) -> tuple[str, str]:
    """`name=path` → `(name, path)`；否则 → `(Path(path).stem, path)`。"""
    if "=" in item:
        name, path = item.split("=", 1)
        name, path = name.strip(), path.strip()
        if name and path:
            return name, path
    return Path(item).stem, item


@router.post("/figures/render")
def render_figures(req: RenderRequest) -> dict:
    """按 figure_specs 渲染 dpi300 PNG；缺文件 → 404，非法图型/缺表 → 422。"""
    import pandas as pd

    tables: dict = {}
    for item in req.result_tables:
        name, path = _split_table_item(item)
        path_obj = Path(path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail=f"结果表文件不存在：{path}")
        tables[name] = pd.read_csv(path_obj)

    try:
        out = plot_tool.render(tables, req.figure_specs, req.out_dir)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    figures = out["figures"]
    return {
        "ok": True,
        "out_dir": str(Path(req.out_dir).resolve()),
        "figures": figures,
        "warnings": out["warnings"],
        "manifest_path": str(Path(out["manifest_path"]).resolve()),
        "dpi_ok": all(f.get("dpi") == 300 for f in figures),
    }
