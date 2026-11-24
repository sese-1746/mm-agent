"""产物清单读写（FR-13 溯源）：登记一行产物 + 按条件过滤分页列出 `artifacts` 表。

**为什么不写进 `store/db.py`**：`store/db.py` 在冻结基线内（README §2.4），
新增查询一律另建模块，避免冻结基线漂移。

分层：路由 `api/routes_artifacts.py` → 本模块。路由层**不裸写 SQL**；
角色（如 `agents/planner.py` 的 `kind='plan'`）也经 `record_artifact()` 登记，不裸写 SQL。
"""
from __future__ import annotations

from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, utcnow

# `artifacts` 全列（README §2.3 逐列核对）——顺序与 schema.sql 定义一致。
ARTIFACT_COLUMNS = ("id", "ts", "kind", "path", "profile_id", "version_id",
                    "producer", "prompt_version", "model_tag", "input_hash")


def record_artifact(kind: str, *, path: str = "", profile_id: str | None = None,
                    version_id: int | None = None, producer: str = "",
                    prompt_version: str | None = None, model_tag: str | None = None,
                    input_hash: str | None = None,
                    db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """登记一行产物，返回 `artifacts.id`（**新增**写入口径，不改表结构）。

    `ts` 统一取 `utcnow()`（与 `store/db.py` 的 ISO8601 UTC 口径一致）；`path` 建议
    用**相对仓库根**的 POSIX 路径（既有行如 `figures/f1.png` 即此口径），仓库外的
    绝对路径原样存——不静默裁剪，免得把「写到别处」记成「写在仓库里」。
    """
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO artifacts (ts, kind, path, profile_id, version_id, producer,"
            " prompt_version, model_tag, input_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (utcnow(), kind, path, profile_id, version_id, producer,
             prompt_version, model_tag, input_hash))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_artifacts(profile_id: str | None = None,
                   version_id: int | None = None,
                   kind: str | None = None,
                   limit: int = 100, offset: int = 0,
                   db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """按 `profile_id` / `version_id` / `kind` 过滤，分页返回产物行。

    返回 `{"rows": [...全列...], "count": <本页行数>, "total": <过滤后总行数>}`：
    `count` 只看本页（受 `limit` 限制），`total` 是**过滤后全集**行数（不受分页影响）。
    空值（`None` / 空串）的过滤条件视为「不设该条件」。按 `id` 降序返回。
    """
    clauses: list[str] = []
    params: list = []
    if profile_id:
        clauses.append("profile_id = ?")
        params.append(profile_id)
    if version_id is not None and str(version_id) != "":
        clauses.append("version_id = ?")
        params.append(int(version_id))
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    limit = max(0, int(limit))
    offset = max(0, int(offset))
    conn = connect(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM artifacts{where}", tuple(params)).fetchone()["n"]
        rows = conn.execute(
            f"SELECT {', '.join(ARTIFACT_COLUMNS)} FROM artifacts{where}"
            " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
    finally:
        conn.close()
    out = [dict(row) for row in rows]
    return {"rows": out, "count": len(out), "total": int(total)}
