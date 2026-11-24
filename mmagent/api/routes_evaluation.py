"""论文与文献综述的评测对照接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..tools import evaluation

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


class EvaluationRow(BaseModel):
    method: str = Field(min_length=1, max_length=120)
    metric: str = Field(min_length=1, max_length=120)
    value: float
    unit: str = ""
    baseline: bool = False
    higher_is_better: bool | None = None
    source: str = ""


class EvaluationRequest(BaseModel):
    scope: str = "paper"
    rows: list[EvaluationRow] = Field(default_factory=list)


@router.post("/evaluation/compare")
def compare_evaluation(req: EvaluationRequest) -> dict:
    """对论文或文献综述中的指标数据做基线对照。"""
    try:
        return evaluation.compare(req.scope, [row.model_dump() for row in req.rows])
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail={"error_code": "EVALUATION_INPUT_INVALID",
                                    "message": str(exc)}) from exc
