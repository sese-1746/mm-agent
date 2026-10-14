"""SQLite 连接工厂：WAL + 外键 + 忙等，schema 初始化。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("data/mm.db")


def utcnow() -> str:
    """当前 UTC 时间，ISO8601 带 Z 后缀。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """打开连接并应用**连接级** PRAGMA；目录不存在时自动创建。

    注意：`journal_mode=WAL` 是**库级持久设置**，切换涉及磁盘 IO（实测每次 ~100ms+），
    故只在建库路径（init_db）执行一次，connect 不再重复设置。
    """
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    # 让 INSERT OR REPLACE 的隐式 DELETE 也触发 BEFORE DELETE 触发器（adopted 不可变守卫）
    conn.execute("PRAGMA recursive_triggers=ON;")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    """按 schema.sql 建库（幂等），并一次性设置库级 PRAGMA（WAL）。"""
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")  # 库级持久设置，建库路径执行一次即可
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return Path(db_path)
