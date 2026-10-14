"""FR-9 档案版本链：append-only，adopted 不可变，草案保留最近 5 版。

设计要点：
- write_version：单事务 BEGIN IMMEDIATE 内取 MAX(version_id)+1，杜绝并发跳号；
  写库失败（如 WAL 锁竞争）重试 3 次、退避 100ms。
- adopt_new_version：永不 UPDATE adopted 行（触发器强制），只 INSERT 新行；
  旧 adopted 版本通过 version_id 比较自然退位。
- AdoptedVersionImmutableError：捕获触发器抛出的 IntegrityError 转换而来，
  并写事件表 error_code='ADOPTED_IMMUTABLE'。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, utcnow

DRAFT_KEEP = 5          # 草案最多保留最近 5 版
MAX_RETRY = 3           # 写库重试次数
BACKOFF_S = 0.1         # 重试退避


class AdoptedVersionImmutableError(Exception):
    """对已 adopted 的档案版本执行 UPDATE/DELETE 时抛出（由触发器转换）。"""


def _emit_event(conn: sqlite3.Connection, event_type: str, actor: str,
                payload: dict, trace_id: str = "") -> None:
    conn.execute(
        "INSERT INTO events (ts, event_type, actor, payload, trace_id) VALUES (?,?,?,?,?)",
        (utcnow(), event_type, actor, json.dumps(payload, ensure_ascii=False),
         trace_id or uuid.uuid4().hex),
    )


def _convert_integrity(conn: sqlite3.Connection, exc: sqlite3.IntegrityError,
                       profile_id: str, version_id) -> None:
    """触发器 'adopted immutable' → 领域异常 + 事件表记录。"""
    if "adopted immutable" in str(exc):
        try:
            _emit_event(conn, "error", "store.profile",
                        {"error_code": "ADOPTED_IMMUTABLE",
                         "profile_id": profile_id, "version_id": version_id})
            conn.commit()
        except sqlite3.Error:
            pass  # 事件写失败不掩盖主异常
        raise AdoptedVersionImmutableError(
            f"profile {profile_id} v{version_id} 已 adopted，禁止修改/删除") from exc
    raise exc


def write_version(profile_id: str, patch: dict, actor: str,
                  upstream_refs: list | None = None,
                  db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """追加一个 draft 版本，返回新 version_id。

    单事务 BEGIN IMMEDIATE 内 SELECT MAX(version_id)+1；写完裁剪草案至最近 5 版；
    锁冲突失败重试 3 次、退避 100ms。
    """
    payload_patch = json.dumps(patch, ensure_ascii=False)
    refs = json.dumps(upstream_refs or [], ensure_ascii=False)

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRY):
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COALESCE(MAX(version_id), 0) + 1 AS nxt FROM profile_doc "
                "WHERE profile_id = ?", (profile_id,)).fetchone()
            version_id = int(row["nxt"])
            conn.execute(
                "INSERT INTO profile_doc (profile_id, version_id, status, patch, actor,"
                " upstream_refs, supersedes, created_at) VALUES (?,?, 'draft', ?,?,?,NULL,?)",
                (profile_id, version_id, payload_patch, actor, refs, utcnow()))
            _emit_event(conn, "profile.write_version", actor,
                        {"profile_id": profile_id, "version_id": version_id,
                         "status": "draft"})
            # 草案裁剪：仅保留最近 DRAFT_KEEP 个 draft
            conn.execute(
                "DELETE FROM profile_doc WHERE profile_id = ? AND status = 'draft'"
                " AND version_id NOT IN ("
                "  SELECT version_id FROM profile_doc WHERE profile_id = ? AND status='draft'"
                "  ORDER BY version_id DESC LIMIT ?)",
                (profile_id, profile_id, DRAFT_KEEP))
            conn.commit()
            return version_id
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            _convert_integrity(conn, exc, profile_id, None)
        except sqlite3.OperationalError as exc:  # database is locked 等
            conn.rollback()
            last_exc = exc
            time.sleep(BACKOFF_S * (attempt + 1))
        finally:
            conn.close()
    raise last_exc  # type: ignore[misc]


def adopt_new_version(profile_id: str, from_version_id: int, actor: str,
                      db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """把 from_version_id 的内容采纳为新的 adopted 版本，返回新 version_id。

    只 INSERT 新行（supersedes=from_version_id），永不 UPDATE adopted 行。
    """
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        src = conn.execute(
            "SELECT patch, upstream_refs FROM profile_doc WHERE profile_id=? AND version_id=?",
            (profile_id, from_version_id)).fetchone()
        if src is None:
            conn.rollback()
            raise KeyError(f"profile {profile_id} v{from_version_id} 不存在")
        row = conn.execute(
            "SELECT COALESCE(MAX(version_id), 0) + 1 AS nxt FROM profile_doc "
            "WHERE profile_id = ?", (profile_id,)).fetchone()
        version_id = int(row["nxt"])
        conn.execute(
            "INSERT INTO profile_doc (profile_id, version_id, status, patch, actor,"
            " upstream_refs, supersedes, created_at) VALUES (?,?, 'adopted', ?,?,?,?,?)",
            (profile_id, version_id, src["patch"], actor, src["upstream_refs"],
             from_version_id, utcnow()))
        _emit_event(conn, "profile.adopt", actor,
                    {"profile_id": profile_id, "version_id": version_id,
                     "supersedes": from_version_id})
        conn.commit()
        return version_id
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        _convert_integrity(conn, exc, profile_id, from_version_id)
    finally:
        conn.close()
    raise AssertionError("unreachable")


def read_profile(profile_id: str, version: str | int = "last",
                 paths: list[str] | None = None,
                 db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """读取档案版本。version='last' 取最新 adopted（无 adopted 则取最新任意状态）。

    paths 非空时只返回 patch 中列出的顶层键。
    """
    conn = connect(db_path)
    try:
        if version == "last":
            row = conn.execute(
                "SELECT * FROM profile_doc WHERE profile_id=? AND status='adopted'"
                " ORDER BY version_id DESC LIMIT 1", (profile_id,)).fetchone()
            if row is None:  # 尚无 adopted，退而取最新版本
                row = conn.execute(
                    "SELECT * FROM profile_doc WHERE profile_id=?"
                    " ORDER BY version_id DESC LIMIT 1", (profile_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM profile_doc WHERE profile_id=? AND version_id=?",
                (profile_id, int(version))).fetchone()
        if row is None:
            raise KeyError(f"profile {profile_id} version={version} 不存在")
        patch = json.loads(row["patch"] or "{}")
        if paths:
            patch = {k: v for k, v in patch.items() if k in set(paths)}
        return {
            "profile_id": row["profile_id"],
            "version_id": row["version_id"],
            "status": row["status"],
            "patch": patch,
            "actor": row["actor"],
            "upstream_refs": json.loads(row["upstream_refs"] or "[]"),
            "supersedes": row["supersedes"],
            "created_at": row["created_at"],
        }
    finally:
        conn.close()


def try_update_row(profile_id: str, version_id: int,
                   db_path: str | Path = DEFAULT_DB_PATH, **fields) -> None:
    """演示/测试用：直接 UPDATE 指定行；对 adopted 行必抛 AdoptedVersionImmutableError。"""
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            for v in fields.values()]
    conn = connect(db_path)
    try:
        try:
            conn.execute(
                f"UPDATE profile_doc SET {cols} WHERE profile_id=? AND version_id=?",
                (*vals, profile_id, version_id))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            _convert_integrity(conn, exc, profile_id, version_id)
    finally:
        conn.close()
