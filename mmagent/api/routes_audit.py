"""审计留痕路由（NFR-2 / FR-13）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..store import audit as audit_store

router = APIRouter(prefix="/api/v1", tags=["audit"])

_OK_TRUE = {"true", "1"}
_OK_FALSE = {"false", "0"}


def _parse_ok(raw: str | None) -> int | None:
    """`ok` 取值域 `{true,false,空}` → `{1,0,None}`；非法值 → 422。"""
    if raw is None or raw == "":
        return None
    low = raw.strip().lower()
    if low in _OK_TRUE:
        return 1
    if low in _OK_FALSE:
        return 0
    raise HTTPException(status_code=422,
                        detail=f"ok 取值须为 true/false/空，实为 {raw!r}")


@router.get("/audit/net")
def list_net_audit(limit: int = Query(50, ge=0), offset: int = Query(0, ge=0),
                   provider: str = "", role: str = "", ok: str = "") -> dict:
    """出网审计台账（`net_audit`，ts 降序）；返回 `{"rows","count","total"}`。"""
    return audit_store.list_net_audit(
        limit=limit, offset=offset, provider=provider or None,
        role=role or None, ok=_parse_ok(ok))


@router.get("/audit/events")
def list_events(limit: int = Query(100, ge=0), offset: int = Query(0, ge=0),
                event_type: str = "", q: str = "") -> dict:
    """事件流水（`events`，ts 降序；`q` 对 payload 子串匹配）→ `{"rows","count","total"}`。"""
    return audit_store.list_events(
        limit=limit, offset=offset, event_type=event_type or None, q=q or None)
