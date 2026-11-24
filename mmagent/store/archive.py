"""档案目录（FR-14）：历年真题与官方论文展示的入库与检索。

**为什么不写进 `store/db.py`**：本仓约定「`store/db.py` 在冻结基线内，新增查询一律另建模块」，
与 `store/artifacts.py` 同一口径。

分层：`api/routes_archive.py` → 本模块（**只写 SQL，不读磁盘、不校验 manifest 语义**）；
`tools/archive_tool.py` → 负责 manifest 解析、路径合法性、`file_present` 现算。

本表 `archive_item` **可变**（同一 `archive_id` 重抓后 upsert 覆盖）——它记的是
「当前磁盘上这份素材是什么」，不是「历史上发生过什么」（后者归 `events`）。
"""
from __future__ import annotations

from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, utcnow

# `archive_item` 全列，顺序与 `schema.sql` 定义一致。
ARCHIVE_COLUMNS = (
    "id", "archive_id", "kind", "year", "problem_code", "tag", "title",
    "source_url", "asset_url", "local_path", "sha256", "bytes", "page_count",
    "fetched_at", "ingested_at", "extra",
)

# 允许的 kind（与 schema.sql 的 CHECK 约束同一集合；此处再挡一次是为了给出可读错误）
KINDS = ("exam_problem", "paper")

# 查询参数的**硬上界**：SQLite INTEGER 是 64 位，Python int 超界绑参会抛 OverflowError
# （在 HTTP 侧就是 500）。这三个常量是唯一真源，API 的 Query 约束与 CLI 共用。
MAX_YEAR = 9999
MAX_LIMIT = 10_000
MAX_OFFSET = 2**63 - 1

_UPSERT_SQL = f"""
INSERT INTO archive_item
    ({', '.join(c for c in ARCHIVE_COLUMNS if c not in ('id', 'ingested_at'))}, ingested_at)
VALUES ({', '.join('?' for _ in ARCHIVE_COLUMNS if _ != 'id')})
ON CONFLICT (archive_id) DO UPDATE SET
    kind         = excluded.kind,
    year         = excluded.year,
    problem_code = excluded.problem_code,
    tag          = excluded.tag,
    title        = excluded.title,
    source_url   = excluded.source_url,
    asset_url    = excluded.asset_url,
    local_path   = excluded.local_path,
    sha256       = excluded.sha256,
    bytes        = excluded.bytes,
    page_count   = excluded.page_count,
    fetched_at   = excluded.fetched_at,
    extra        = excluded.extra,
    ingested_at  = excluded.ingested_at
"""


def upsert_items(rows: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """按 `archive_id` 幂等写入（存在则整行覆盖，不存在则插入）。

    返回 `{"inserted": n, "updated": n}`：`inserted + updated == len(rows)`。
    计数靠写入前 `SELECT` 一次已有 archive_id 集合得出（行数量级为几十，不必优化）。
    """
    if not rows:
        return {"inserted": 0, "updated": 0}
    ids = [r["archive_id"] for r in rows]
    if len(set(ids)) != len(ids):
        dup = sorted({x for x in ids if ids.count(x) > 1})
        raise ValueError(f"manifest 内 archive_id 重复：{dup}")
    now = utcnow()
    conn = connect(db_path)
    try:
        marks = ", ".join("?" for _ in ids)
        existing = {r[0] for r in conn.execute(
            f"SELECT archive_id FROM archive_item WHERE archive_id IN ({marks})",
            tuple(ids))}
        for r in rows:
            conn.execute(_UPSERT_SQL, (
                r["archive_id"], r["kind"], int(r["year"]),
                r.get("problem_code") or "", r.get("tag") or "",
                r.get("title") or "", r.get("source_url") or "",
                r.get("asset_url") or "", r.get("local_path") or "",
                r.get("sha256") or "", r.get("bytes"),
                r.get("page_count"), r["fetched_at"], r.get("extra") or "{}",
                now,
            ))
        conn.commit()
    finally:
        conn.close()
    return {"inserted": len([i for i in ids if i not in existing]),
            "updated": len([i for i in ids if i in existing])}


def list_items(year: int | None = None, kind: str | None = None,
               problem_code: str | None = None, q: str | None = None,
               limit: int = 200, offset: int = 0,
               db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """按年/类型/题号/关键词过滤并分页。

    `q` 对 `title` 与 `archive_id` 做 `LIKE '%…%'`（大小写不敏感）；
    空串与 `None` 一律视为「不设该条件」。
    返回 `{"rows": [...], "count": <本页>, "total": <过滤后全集>}`，按 (year DESC, kind, id) 排序。
    """
    clauses: list[str] = []
    params: list = []
    if year is not None:
        # 上界不是洁癖：`year=999999999999999999999` 曾让 sqlite3 绑参抛 OverflowError
        # （Python int 超出 SQLite INTEGER），在 `/archive/list` 上以 **HTTP 500** 冒出来。
        year = int(year)
        if not (1 <= year <= MAX_YEAR):
            raise ValueError(f"year 必须在 1..{MAX_YEAR}，收到 {year}")
        clauses.append("year = ?")
        params.append(year)
    if kind:
        if kind not in KINDS:
            raise ValueError(f"kind 只能是 {KINDS}，收到 {kind!r}")
        clauses.append("kind = ?")
        params.append(kind)
    if problem_code:
        clauses.append("problem_code = ?")
        params.append(problem_code)
    if q:
        # `LIKE` 里 `%` / `_` 是通配符，`q` 是**用户输入**，必须转义后配 `ESCAPE`。
        # 不转义的后果（gk-r27 实测）：`q="%"` 命中全部 33 条、`q="A%196"` 误命中 `paper-2024-B196`、
        # `q="A_196"` 漏命中——参数是绑定的（无注入），但前端搜索会出现假阴性/假阳性。
        clauses.append("(title LIKE ? ESCAPE '\\' OR archive_id LIKE ? ESCAPE '\\')")
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        params.extend([like, like])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        raise ValueError(f"offset 必须是整数，收到 {offset!r}")
    if not (0 <= offset <= MAX_OFFSET):
        raise ValueError(f"offset 必须在 0..{MAX_OFFSET}，收到 {offset}")
    if limit is None:
        # `None` = 不分页（给「统计/全量扫描」这类内部调用用）；仍保留 offset 语义
        page_sql, page_params = " LIMIT -1 OFFSET ?", [offset]
    else:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError(f"limit 必须是整数，收到 {limit!r}")
        if not (0 <= limit <= MAX_LIMIT):
            raise ValueError(f"limit 必须在 0..{MAX_LIMIT}，收到 {limit}")
        page_sql, page_params = " LIMIT ? OFFSET ?", [limit, offset]
    conn = connect(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM archive_item{where}", tuple(params)).fetchone()["n"]
        rows = conn.execute(
            f"SELECT {', '.join(ARCHIVE_COLUMNS)} FROM archive_item{where}"
            f" ORDER BY year DESC, kind, problem_code, tag, id{page_sql}",
            (*params, *page_params)).fetchall()
    finally:
        conn.close()
    out = [dict(r) for r in rows]
    return {"rows": out, "count": len(out), "total": int(total)}


def summary(db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """档案总数与按年/按类型的分组计数（供前端「查看所有」抬头展示）。"""
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM archive_item").fetchone()["n"]
        by_kind = {r["kind"]: int(r["n"]) for r in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM archive_item GROUP BY kind")}
        by_year_rows = conn.execute(
            "SELECT year, kind, COUNT(*) AS n FROM archive_item"
            " GROUP BY year, kind ORDER BY year DESC").fetchall()
    finally:
        conn.close()
    years: dict[int, dict] = {}
    for r in by_year_rows:
        row = years.setdefault(int(r["year"]), {"year": int(r["year"]), "total": 0,
                                                "exam_problem": 0, "paper": 0})
        row[r["kind"]] = int(r["n"])
        row["total"] += int(r["n"])
    return {
        "total": int(total),
        "by_kind": {"exam_problem": int(by_kind.get("exam_problem", 0)),
                    "paper": int(by_kind.get("paper", 0))},
        "by_year": [years[y] for y in sorted(years, reverse=True)],
        "years": sorted(years, reverse=True),
    }


def get_item(archive_id: str, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    """取单条档案；不存在返回 `None`（由路由层转 404，不抛异常）。"""
    conn = connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {', '.join(ARCHIVE_COLUMNS)} FROM archive_item WHERE archive_id = ?",
            (archive_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None
