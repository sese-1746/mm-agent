"""产物清单路由（FR-13 溯源）。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..store import artifacts as artifacts_store

router = APIRouter(prefix="/api/v1", tags=["artifacts"])


@router.get("/artifacts/list")
def list_artifacts(profile_id: str | None = None,
                   version_id: int | None = None,
                   kind: str | None = None,
                   limit: int = Query(100, ge=0),
                   offset: int = Query(0, ge=0)) -> dict:
    """按条件过滤并分页列出产物；返回 `{"rows","count","total"}`。"""
    return artifacts_store.list_artifacts(
        profile_id=profile_id or None, version_id=version_id,
        kind=kind or None, limit=limit, offset=offset)
