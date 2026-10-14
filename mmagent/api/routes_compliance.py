"""合规相关路由。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..tools import compliance_tool

router = APIRouter(prefix="/api/v1", tags=["compliance"])


class DisclosureRequest(BaseModel):
    """AI 使用披露生成请求体。"""
    profile_id: str
    version_id: int | None = None
    output_dir: str
    human_edit_ratio: float | None = None


@router.post("/compliance/disclosure")
def make_disclosure(req: DisclosureRequest) -> dict:
    """生成 ai_disclosure.md 与 .docx，返回落盘路径。"""
    md_path = compliance_tool.make_disclosure(
        req.profile_id, req.version_id, req.output_dir,
        human_edit_ratio=req.human_edit_ratio)
    return {"md_path": str(md_path),
            "docx_path": str(md_path.with_suffix(".docx"))}
