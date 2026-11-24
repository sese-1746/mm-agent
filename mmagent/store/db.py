"""SQLite 连接工厂：WAL + 外键 + 忙等，schema 初始化。

另含 **不可变违规的连接层审计留痕**（ISSUE-FR9-03，2026-10-16 A7）：
`profile_doc` 的 adopted 不可变守卫是 **SQL 触发器**，`RAISE(ABORT)` 会连同触发器**自身**的
`INSERT INTO events` 一起回滚（实测：ABORT / FAIL 留痕均为 0 行；IGNORE 能留痕但不再抛
`IntegrityError`）。因此裸 SQL 路径（不经 `store/profile.py`）无法在 SQL 层留痕。
处置：`connect()` 返回的连接改为 `_GuardedConnection` 子类，在 `execute`/`executemany` 捕获
`IntegrityError`（消息含 `adopted immutable`）后，先回滚被 ABORT 的语句，再单独写一条
`events(event_type='error', payload={"error_code":"ADOPTED_IMMUTABLE",…})` 并提交，然后原样重抛。
这样**一次违规恰好留一条痕**，且不改变异常的对外语义。
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("data/mm.db")

IMMUTABLE_ERROR_CODE = "ADOPTED_IMMUTABLE"
# 触发器守卫的报错文案（`schema.sql` 里 RAISE(ABORT, 'adopted immutable') 用同一字符串）。
# 抽成常量并在**测试中引用同一常量**：将来改文案时测试会一起红，而不是静默失效。
IMMUTABLE_TRIGGER_MSG = "adopted immutable"

# 运行期 ensure-schema（2026-10-16 task-002 起）：既有 `.db` 文件在新对象（表/索引/触发器）加入后
# 必须能自动拿到它们（否则 `no such table: net_audit`、或 FR-9 的 adopted 不可变守卫**静默缺失**）。
# **唯一真源 = `store/schema.sql`**：本模块**不持有任何 DDL 文本**，运行期从 `schema.sql` 抽取
# `CREATE TABLE/INDEX/TRIGGER IF NOT EXISTS` 语句，按需补执行（SELECT-first，既有库零写事务）。
#
# 切句用 **`sqlite3.complete_statement()`**（纯 stdlib，走 SQLite 自身解析器）逐行累积判完整：
# 触发器体 `BEGIN … END;` 内含 `;`，任何「按 `;` 切」的正则都会切坏语句 —— 这正是 A0-7 修掉的
# 「静默部分应用」缺陷（只补表/索引、触发器静默不建）。
# 只抽 `IF NOT EXISTS` 形式（`schema.sql` 全量如此）→ 重复执行天然幂等。
_SCHEMA_OBJECT_RE = re.compile(
    r"^[ \t]*CREATE\s+(TABLE|INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_]\w*)",
    re.IGNORECASE | re.MULTILINE)
# 补建顺序：表 → 索引 → 触发器（索引与触发器都依赖表）
_KIND_ORDER = {"table": 0, "index": 1, "trigger": 2}


def iter_schema_statements(schema_text: str) -> list[tuple[str, str, str]]:
    """按 `sqlite3.complete_statement()` 逐行切分 SQL → `[(kind, name, stmt), …]`。

    `kind` ∈ `{"table", "index", "trigger"}`（与 `sqlite_master.type` 同词）；
    保序为 **表 → 索引 → 触发器**。非 `CREATE … IF NOT EXISTS` 的语句（如纯注释块）跳过。
    """
    out: list[tuple[str, str, str]] = []
    buf: list[str] = []
    for line in schema_text.splitlines(keepends=True):
        buf.append(line)
        candidate = "".join(buf)
        if not sqlite3.complete_statement(candidate):
            continue                      # 还没到语句末尾（触发器体里的 `;` 不会误判）
        buf = []
        stmt = candidate.strip()
        m = _SCHEMA_OBJECT_RE.search(stmt)      # MULTILINE：跳过语句前的注释行
        if not m:
            continue
        out.append((m.group(1).lower(), m.group(2), stmt))
    out.sort(key=lambda o: _KIND_ORDER[o[0]])   # stable：同类保持文件内相对顺序
    return out


def schema_objects(schema_text: str | None = None) -> list[tuple[str, str, str]]:
    """从 `schema.sql` 抽取可运行期补建的对象 → `[(kind, name, stmt), …]`。

    `schema_text` 可显式传入（测试可喂「变异后」的 schema 文本）。
    """
    text = SCHEMA_PATH.read_text(encoding="utf-8") if schema_text is None else schema_text
    return iter_schema_statements(text)


def utcnow() -> str:
    """当前 UTC 时间，ISO8601 带 Z 后缀。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _extract_profile_refs(sql: str, parameters) -> tuple[object, object]:
    """从违规语句回推 `(profile_id, version_id)`，供审计 payload 使用；无法判定时返回 (None, None)。

    仅覆盖本仓实际存在的两种形状（不做通用 SQL 解析）：
    - `UPDATE profile_doc SET … WHERE profile_id=? AND version_id=?` → 取**尾两个**参数；
    - `INSERT [OR REPLACE] INTO profile_doc … VALUES (?, …)` → 取**前两个**参数（列序即 profile_id, version_id）。
    """
    if isinstance(parameters, dict):
        pid = parameters.get("profile_id", parameters.get(":profile_id"))
        vid = parameters.get("version_id", parameters.get(":version_id"))
        return pid, vid
    if not isinstance(parameters, (tuple, list)):
        return None, None
    vals = list(parameters)
    head = " ".join(str(sql).split()).upper()
    if head.startswith("UPDATE") and len(vals) >= 2:
        return vals[-2], vals[-1]
    if len(vals) >= 2:
        return vals[0], vals[1]
    return None, None


class _GuardedConnection(sqlite3.Connection):
    """连接层守卫：把触发器的 `adopted immutable` ABORT 补记为 events 审计留痕。"""

    def _record_immutable_violation(self, sql: str, parameters) -> None:
        try:
            sqlite3.Connection.rollback(self)          # 清掉被 ABORT 的事务，避免携带残留写锁
        except sqlite3.Error:
            pass
        payload = {"error_code": IMMUTABLE_ERROR_CODE}
        profile_id, version_id = _extract_profile_refs(sql, parameters)
        if profile_id is not None:
            payload["profile_id"] = profile_id
        if version_id is not None:
            payload["version_id"] = version_id
        try:
            sqlite3.Connection.execute(
                self,
                "INSERT INTO events (ts, event_type, actor, payload, trace_id)"
                " VALUES (?,?,?,?,?)",
                (utcnow(), "error", "store.profile",
                 json.dumps(payload, ensure_ascii=False), uuid.uuid4().hex))
            sqlite3.Connection.commit(self)
        except sqlite3.Error:
            pass  # 留痕失败不掩盖主异常（与 store/profile.py 既有策略一致）

    def execute(self, sql, parameters=()):  # type: ignore[override]
        try:
            return sqlite3.Connection.execute(self, sql, parameters)
        except sqlite3.IntegrityError as exc:
            if IMMUTABLE_TRIGGER_MSG in str(exc):
                self._record_immutable_violation(sql, parameters)
            raise

    def executemany(self, sql, seq_of_parameters):  # type: ignore[override]
        try:
            return sqlite3.Connection.executemany(self, sql, seq_of_parameters)
        except sqlite3.IntegrityError as exc:
            if IMMUTABLE_TRIGGER_MSG in str(exc):
                self._record_immutable_violation(sql, None)
            raise


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    """运行期 ensure-schema：把「后加的对象」补建到**既有**库文件上（幂等）。

    为什么需要：`schema.sql` 只在 `init_db()` 执行一次；旧 `.db` 文件不会自动获得
    新增的 `net_audit` 表、也**不会**获得新增/缺失的触发器（FR-9 的 adopted 不可变守卫
    一旦缺失就是 P0 保证的**静默失效**）→ 运行期会出现 `no such table: net_audit`
    或「不可变守卫不再拦 REPLACE」。

    机制（**唯一真源 = `schema.sql`**）：
    - 覆盖 **表 / 索引 / 触发器**（`schema_objects()` 用 `sqlite3.complete_statement()` 切句，
      故 `BEGIN … END;` 的触发器体不会被切坏）；
    - 先 `SELECT … FROM sqlite_master WHERE type=? AND name=?`，**仅当缺失时**才执行对应
      `CREATE … IF NOT EXISTS`（既有库命中 SELECT 即返回 → **不产生写事务**、不干扰并发路径）；
    - DDL 文本从 `schema.sql` 现取 → 不存在「改一处忘一处」的第二份定义。

    返回本次**实际执行**的语句列表（无需变更时 `[]`）。
    """
    created: list[str] = []
    for kind, name, stmt in schema_objects():
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?", (kind, name)).fetchone()
        if row is not None:
            continue
        conn.execute(stmt)
        created.append(stmt)
    if created:
        conn.commit()
    return created


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """打开连接并应用**连接级** PRAGMA；目录不存在时自动创建。

    注意：`journal_mode=WAL` 是**库级持久设置**，切换涉及磁盘 IO（实测每次 ~100ms+），
    故只在建库路径（init_db）执行一次，connect 不再重复设置。

    另在 `busy_timeout` 生效后调用 `ensure_schema()`：把后加的表（如 `net_audit`）补建到
    **既有**库文件上，避免旧库出现 `no such table`（幂等；既有库命中 SELECT 直接返回，无写事务）。
    """
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, factory=_GuardedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    # 让 INSERT OR REPLACE 的隐式 DELETE 也触发 BEFORE DELETE 触发器（adopted 不可变守卫）
    conn.execute("PRAGMA recursive_triggers=ON;")
    ensure_schema(conn)   # 幂等补建后加的表（旧 .db 文件也能拿到 net_audit）
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
