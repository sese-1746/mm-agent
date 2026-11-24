"""体检相关路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..tools import intake_tool
from . import queue_gate

router = APIRouter(prefix="/api/v1", tags=["intake"])


class IntakeRequest(BaseModel):
    """体检请求体。"""
    dataset_dir: str
    out_dir: str


@router.post("/intake/run")
def run_intake(req: IntakeRequest) -> dict:
    """对数据集目录执行附件体检（确定性），返回汇总与产物路径。

    执行体套进程内并发闸：满额 429 `QUEUE_FULL`（见 `api/queue_gate.py`）。
    """
    with queue_gate.long_task_slot():
        try:
            return intake_tool.run_intake(req.dataset_dir, req.out_dir)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
