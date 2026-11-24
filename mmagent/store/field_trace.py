"""FR-9 字段级溯源：读写 `profile_field_trace`（每个档案字段的产出角色/时刻/版本/上游）。

**为什么不写进 `store/db.py`**：`store/db.py` 在冻结基线内（README §5 第 3 条），
新增查询一律另建模块，避免冻结基线漂移。

为什么 `upstream_refs` 不做成 `artifacts` 上的列（A4 原案）：`store/db.py::ensure_schema()`
只认 `CREATE … IF NOT EXISTS`，`ALTER TABLE ADD COLUMN` 既不会被补建到既有 `.db`，也会让
`init_db()` 的 `executescript()` 第二次执行炸在 `duplicate column name`（README §1）。
故 A4 的四要素（producer / produced_at / version_id / upstream_refs）落在**本表四个列**上。

分层：路由/CLI → 本模块。调用方**不裸写 SQL**。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, utcnow
from .profile import read_profile

# `profile_field_trace` 全列——顺序与 schema.sql 定义一致。
FIELD_TRACE_COLUMNS = ("id", "ts", "profile_id", "version_id", "field_path",
                       "producer", "produced_at", "upstream_refs", "trace_id")


def record_field_trace(profile_id: str, version_id: int, fields: dict,
                       *, producer: str = "", trace_id: str = "",
                       db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """批量写入字段级溯源行，返回写入行数。

    `fields` 形如 `{"paper.title": {"producer": "archivist", "produced_at": "...",
    "upstream_refs": ["artifact:1"]}, …}`：`field_path` 为键，值为可选属性字典，
    其中 `producer` 覆盖入参默认值、`upstream_refs`(list) 以 UTF-8 JSON 落库、
    `produced_at` 缺省取当前 UTC 时刻；`trace_id` 缺省本次调用统一生成一个。
    """
    if not fields:
        return 0
    now = utcnow()
    default_trace = trace_id or uuid.uuid4().hex
    rows: list[tuple] = []
    for field_path, spec in fields.items():
        spec = spec or {}
        refs = spec.get("upstream_refs") or []
        if not isinstance(refs, (list, tuple)):
            refs = [refs]        # 单值容错：保证读回恒为 list（契约见 list_field_traces）
        rows.append((
            now,
            profile_id,
            int(version_id),
            str(field_path),
            spec.get("producer", producer) or "",
            spec.get("produced_at") or now,
            json.dumps(list(refs), ensure_ascii=False),
            spec.get("trace_id") or default_trace,
        ))
    conn = connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO profile_field_trace"
            " (ts, profile_id, version_id, field_path, producer, produced_at,"
            "  upstream_refs, trace_id) VALUES (?,?,?,?,?,?,?,?)",
            rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def list_field_traces(profile_id: str, version: int | str | None = None,
                      field_path: str | None = None, *, limit: int = 1000,
                      db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """按档案版本/字段路径查询溯源行，按 `id` 升序。

    返回 `{"rows": [...全列...], "count": <本页行数>, "total": <过滤后总数>}`。
    `version=None` → 取该档案**最新**版本（口径复用 `store.profile.read_profile(…, "last")`：
    优先最新 adopted，无 adopted 则退最新任意状态）。`upstream_refs` 解析回 **list**。
    档案/版本不存在时抛 `KeyError`（由调用方转 404 / exit 2）。
    """
    if version is None or str(version) == "" or version == "last":
        version_id = int(read_profile(profile_id, "last", db_path=db_path)["version_id"])
    else:
        version_id = int(version)
        read_profile(profile_id, version_id, db_path=db_path)  # 校验存在性（KeyError）
    clauses = ["profile_id = ?", "version_id = ?"]
    params: list = [profile_id, version_id]
    if field_path:
        clauses.append("field_path = ?")
        params.append(field_path)
    where = " WHERE " + " AND ".join(clauses)

    conn = connect(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM profile_field_trace{where}",
            tuple(params)).fetchone()["n"]
        rows = conn.execute(
            f"SELECT {', '.join(FIELD_TRACE_COLUMNS)} FROM profile_field_trace{where}"
            " ORDER BY id ASC LIMIT ?", (*params, max(0, int(limit)))).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["upstream_refs"] = json.loads(item["upstream_refs"] or "[]")
        out.append(item)
    return {"rows": out, "count": len(out), "total": int(total)}
