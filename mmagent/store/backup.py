"""NFR-5 备份原语：`sqlite3.Connection.backup()` 在线热备 + `backup_log` 台账。

**为什么不写进 `store/db.py`**：`store/db.py` 在冻结基线内（README §5 第 3 条），
新增能力一律另建模块，避免冻结基线漂移。

为什么必须用 `Connection.backup()` 而**不用** `shutil.copy*`：本库跑在 WAL 模式下，
`-wal` 里可能还有未 checkpoint 的页；裸拷 `.db` 拿到的是**不一致快照**（可能撕裂到某个
中间事务）。`backup()` 走 SQLite 自身的在线备份 API，对源库加读锁、逐页复制，得到一致快照。

台账口径：**成功写一行（`ok=1`、`bytes`=目标文件大小）**；**失败也写一行（`ok=0`、
`error`=异常摘要）后再原样重抛**——备份失败而不留痕等于台账说谎，比不做台账更糟。
本轮只交付原语（30 分钟定时调度见 README §8 范围外）。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, utcnow

# `backup_log` 全列——顺序与 schema.sql 定义一致。
BACKUP_LOG_COLUMNS = ("id", "ts", "db_path", "backup_path", "bytes", "ok", "error")

# 异常摘要上限：台账是给人看的，堆栈/长消息只留前 500 字。
_ERROR_MAX = 500


def _compact_ts() -> str:
    """文件名安全的 UTC 时间戳（`utcnow()` 带冒号，Windows 文件名不允许）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _summarize(exc: BaseException) -> str:
    """异常 → 一行摘要（类型 + 消息，截断到 `_ERROR_MAX`）。"""
    return f"{type(exc).__name__}: {exc}"[:_ERROR_MAX]


def _write_log(*, ts: str, src: Path, dst: Path, nbytes: int | None,
               ok: bool, error: str | None,
               db_path: str | Path) -> int:
    """向 `backup_log` 追加一行，返回 `log_id`（异常向上抛，由调用方决定是否掩蔽）。"""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO backup_log (ts, db_path, backup_path, bytes, ok, error)"
            " VALUES (?,?,?,?,?,?)",
            (ts, str(src), str(dst), nbytes, 1 if ok else 0, error))
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def backup_db(src: str | Path, dst: str | Path | None = None, *,
              db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """把 `src` 库在线热备到 `dst`（缺省 `<src>.bak.<UTC ts>`，同目录），并记台账。

    返回 `{"ok": True, "src": …, "dst": …, "bytes": 目标文件大小, "log_id": …}`。
    失败：先写一行 `ok=0` 的台账，再**原样重抛**（`log_id` 不返回——调用方拿不到正常结果）。
    """
    src_path = Path(src)
    dst_path = Path(dst) if dst is not None else src_path.with_name(
        f"{src_path.name}.bak.{_compact_ts()}")
    ts = utcnow()
    try:
        if not src_path.exists():
            raise FileNotFoundError(f"源库不存在：{src_path}")
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(str(src_path), timeout=5.0)
        try:
            target = sqlite3.connect(str(dst_path))
            try:
                source.backup(target)      # 在线备份 API：WAL 下也拿到一致快照
            finally:
                target.close()
        finally:
            source.close()
        nbytes = dst_path.stat().st_size
    except Exception as exc:  # noqa: BLE001  失败也要留痕，然后原样重抛
        try:
            _write_log(ts=ts, src=src_path, dst=dst_path, nbytes=None,
                       ok=False, error=_summarize(exc), db_path=db_path)
        except Exception as log_exc:  # noqa: BLE001  留痕失败不掩盖主异常
            print(f"[backup] 失败台账写入失败（src={src_path}）：{_summarize(log_exc)}",
                  file=sys.stderr)
        raise

    log_id = _write_log(ts=ts, src=src_path, dst=dst_path, nbytes=nbytes,
                        ok=True, error=None, db_path=db_path)
    return {"ok": True, "src": str(src_path), "dst": str(dst_path),
            "bytes": nbytes, "log_id": log_id}


def list_backups(*, limit: int = 100, db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """按 `id` 降序列出台账。

    返回 `{"rows": [...全列...], "count": <本页行数>, "total": <全表行数>}`。
    """
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM backup_log").fetchone()["n"]
        rows = conn.execute(
            f"SELECT {', '.join(BACKUP_LOG_COLUMNS)} FROM backup_log"
            " ORDER BY id DESC LIMIT ?", (max(0, int(limit)),)).fetchall()
    finally:
        conn.close()
    out = [dict(row) for row in rows]
    return {"rows": out, "count": len(out), "total": int(total)}
