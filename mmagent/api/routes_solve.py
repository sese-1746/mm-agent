"""求解相关路由（守卫 + 执行）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..pipeline.guard import ScriptGuardError
from ..tools import solve_tool

router = APIRouter(prefix="/api/v1", tags=["solve"])


class SolveRequest(BaseModel):
    """脚本执行请求体。"""
    script_path: str
    workdir: str
    timeout_s: int = 300


@router.post("/solve/run")
def run_solve(req: SolveRequest) -> dict:
    """先 AST 守卫再沙箱执行脚本；守卫违规返回 422。"""
    try:
        return solve_tool.validate_and_run(req.script_path, req.workdir,
                                           timeout_s=req.timeout_s)
    except ScriptGuardError as exc:
        raise HTTPException(status_code=422,
                            detail={"violations": exc.violations}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
