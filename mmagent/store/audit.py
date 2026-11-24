"""审计查询（NFR-2 / FR-13 联动）：`net_audit` 出网台账 + `events` 事件流水。

**为什么不写进 `store/db.py`**：`store/db.py` 在冻结基线内（README §2.4），
新增查询一律另建模块，避免冻结基线漂移。

分层：路由 `api/routes_audit.py` → 本模块。路由层**不裸写 SQL**。

列名逐列核对 README §2.3（**两张表都没有 `run_id` 列**）：
- `net_audit`：`id / ts / provider / endpoint / model / role / prompt_hash / ok / error_code`
- `events`：`id / ts / event_type / actor / payload(JSON) / trace_id`
"""
from __future__ import annotations

from pathlib import Path

from .db import DEFAULT_DB_PATH, connect

# 两张表的全列（README §2.3；顺序与 schema.sql 定义一致）。
NET_AUDIT_COLUMNS = ("id", "ts", "provider", "endpoint", "model", "role",
                     "prompt_hash", "ok", "error_code")
EVENT_COLUMNS = ("id", "ts", "event_type", "actor", "payload", "trace_id")


def _page(sql_select: str, sql_count: str, where: str, params: list,
          limit: int, offset: int, db_path: str | Path) -> dict:
    """共用分页：返回 `{"rows","count","total"}`（`count` 只看本页）。"""
    limit = max(0, int(limit))
    offset = max(0, int(offset))
    conn = connect(db_path)
    try:
        total = conn.execute(sql_count + where, tuple(params)).fetchone()["n"]
        rows = conn.execute(
            f"{sql_select}{where} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
    finally:
        conn.close()
    out = [dict(row) for row in rows]
    return {"rows": out, "count": len(out), "total": int(total)}


def list_net_audit(limit: int = 50, offset: int = 0,
                   provider: str | None = None, role: str | None = None,
                   ok: int | None = None,
                   db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """列出 `net_audit`（`ts` 降序）；`ok` ∈ `{1, 0, None}`（`None` = 不筛）。"""
    clauses: list[str] = []
    params: list = []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if role:
        clauses.append("role = ?")
        params.append(role)
    if ok is not None:
        clauses.append("ok = ?")
        params.append(int(ok))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return _page(f"SELECT {', '.join(NET_AUDIT_COLUMNS)} FROM net_audit",
                 "SELECT COUNT(*) AS n FROM net_audit",
                 where, params, limit, offset, db_path)


def list_events_since(after_id: int = 0, run_id: str | None = None,
                      limit: int = 200,
                      db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    """按 `id` **升序**取 `id > after_id` 的事件（SSE 增量轮询的唯一查询入口）。

    `events` 表**没有** `run_id` 列（6 列：`id/ts/event_type/actor/payload/trace_id`），
    所以按 run 过滤只能走 `json_extract(payload,'$.run_id')`：无表达式索引，是全表扫描
    （当前表量级 ~数十行，可接受）。`json_valid` 前置避免个别畸形 payload 直接报错。

    `run_id` 为 `None` → **不过滤**，返回全表增量（调用方不得据此宣称事件属于某个 run）。
    """
    clauses = ["id > ?"]
    params: list = [int(after_id)]
    if run_id is not None:
        clauses.append("json_valid(payload)")
        clauses.append("json_extract(payload, '$.run_id') = ?")
        params.append(run_id)
    params.append(max(0, int(limit)))
    sql = (f"SELECT {', '.join(EVENT_COLUMNS)} FROM events"
           f" WHERE {' AND '.join(clauses)} ORDER BY id ASC LIMIT ?")
    conn = connect(db_path)
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def list_events(limit: int = 100, offset: int = 0,
                event_type: str | None = None, q: str | None = None,
                db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """列出 `events`（`ts` 降序）；`q` 对 `payload` 做**子串匹配**（参数化 LIKE）。"""
    clauses: list[str] = []
    params: list = []
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if q:
        clauses.append("payload LIKE ?")
        params.append(f"%{q}%")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return _page(f"SELECT {', '.join(EVENT_COLUMNS)} FROM events",
                 "SELECT COUNT(*) AS n FROM events",
                 where, params, limit, offset, db_path)
