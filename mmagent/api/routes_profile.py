"""档案相关路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..tools import profile_tool

router = APIRouter(prefix="/api/v1", tags=["profile"])


@router.get("/profile/{profile_id}")
def get_profile(profile_id: str, version: str = "last") -> dict:
    """读取档案版本；version='last' 取最新 adopted。"""
    try:
        return profile_tool.read(profile_id, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
