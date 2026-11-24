"""档案相关路由。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..tools import profile_tool

router = APIRouter(prefix="/api/v1", tags=["profile"])


class ProfileWriteRequest(BaseModel):
    """档案写入请求体（字段逐字对齐 `profile_tool.write_version` 形参）。"""
    profile_id: str
    patch: dict
    actor: str
    upstream_refs: list | None = None


# ⚠️ 静态路径必须注册在 `/profile/{profile_id}` **之前**：Starlette 按注册顺序匹配，
# 否则 `GET /profile/show` 会被动态路由吞成 profile_id="show"（实测 404 NOT_FOUND）。
@router.get("/profile/show")
def show_profile(id: str, version: str = "last") -> dict:
    """按 query 读取档案版本（等价 `GET /profile/{id}?version=`）；缺 `id` 走 422 校验错误。"""
    try:
        return profile_tool.read(id, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/profile/write")
def write_profile(req: ProfileWriteRequest) -> dict:
    """追加一个 draft 档案版本（仅转发 store，不做校验、不改 store 行为）。"""
    try:
        version_id = profile_tool.write_version(req.profile_id, req.patch, req.actor,
                                                req.upstream_refs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"profile_id": req.profile_id, "version_id": version_id}


@router.get("/profile/{profile_id}")
def get_profile(profile_id: str, version: str = "last") -> dict:
    """读取档案版本；version='last' 取最新 adopted。"""
    try:
        return profile_tool.read(profile_id, version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/profile/{profile_id}/versions")
def list_profile_versions(profile_id: str) -> dict:
    """列出该档案全部版本（按 version_id 降序）；档案不存在返回 404。"""
    try:
        versions = profile_tool.list_versions(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"profile_id": profile_id, "versions": versions}
