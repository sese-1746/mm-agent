"""文献综述路由：多个 Word/PDF 文献 → 一份可编辑的简易综述。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..tools import literature_review
from ..store.artifacts import record_artifact
from ..store.db import DEFAULT_DB_PATH

router = APIRouter(prefix="/api/v1", tags=["literature-review"])


class ReviewDocument(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    b64: str = Field(min_length=1)


class ReviewRequest(BaseModel):
    documents: list[ReviewDocument] = Field(default_factory=list)
    title: str = Field(default="数模论文文献综述", max_length=200)


@router.post("/review/generate")
def generate_review(req: ReviewRequest) -> dict:
    """接收 base64 编码的 .docx/.pdf 文献，生成 Markdown 与 Word 综述。"""
    try:
        result = literature_review.generate_review(
            [item.model_dump() for item in req.documents], title=req.title)
        # 让项目台账能够追踪综述 Word 产物；数据库按项目默认库自动初始化。
        try:
            record_artifact("literature_review", path=result["output_docx"],
                            producer="literature_review", db_path=DEFAULT_DB_PATH)
        except Exception as exc:  # 产物已成功写出，台账故障不应掩盖交付结果
            result["ledger_warning"] = f"综述已生成，但项目台账登记失败：{exc}"
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail={"error_code": "REVIEW_INPUT_INVALID",
                                    "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail={"error_code": "REVIEW_WRITE_FAILED",
                                    "message": f"文献综述写盘失败：{exc}"}) from exc
