"""FR-14 档案库路由：真题 PDF / 论文逐页 JPEG 的检索与读取。

五个端点（前缀 `/api/v1`）：

- `GET /archive/list`      过滤 + 分页（每行带**现算**的 `file_present`）
- `GET /archive/summary`   统计 + 文件缺失数 + manifest 存在性
- `GET /archive/item/{id}` 单条详情
- `GET /archive/page/{id}/{page_no}` 论文第 n 页 JPEG
- `GET /archive/file/{id}` 真题 PDF

**零网络**：素材是本地文件，本文件不得 import `urllib` / `socket` / `httpx` / `requests` /
`ftplib` / `http`（`cli netguard-check` 会判违规）。

错误码（统一错误体由 `app.py` 的处理器拼装）：

- `ARCHIVE_NOT_FOUND`（404）：档案不在库里；
- `ARCHIVE_BAD_PAGE`（400）：真题传了 `page_no` / 论文页码越界 / `page_no` 缺省；
- `ARCHIVE_FILE_MISSING`（404）：库里有记录但磁盘上没文件（素材未抓取或被清理）；
- `ARCHIVE_BAD_QUERY`（400）：`kind` 不在 `("exam_problem","paper")`（**本实现补充**，
  文档未列举；不加则 `kind=exam` 会以 500 冒出来）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..store import archive as archive_store
from ..store.db import DEFAULT_DB_PATH
from ..tools import archive_tool

router = APIRouter(prefix="/api/v1", tags=["archive"])

# 读库路径（模块级便于测试隔离；默认与 CLI 一致）。
ARCHIVE_DB: str | Path = DEFAULT_DB_PATH


def _not_found(archive_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail={
        "error_code": "ARCHIVE_NOT_FOUND", "message": f"档案不存在：{archive_id}"})


@router.get("/archive/list")
def archive_list(year: int | None = Query(None, ge=1, le=archive_store.MAX_YEAR),
                 kind: str | None = None,
                 problem_code: str | None = None, q: str | None = None,
                 limit: int = Query(200, ge=0, le=archive_store.MAX_LIMIT),
                 offset: int = Query(0, ge=0, le=archive_store.MAX_OFFSET)) -> dict:
    """档案清单：按年/类型/题号/关键词过滤，`limit`/`offset` 分页。

    上下界不是洁癖：`?year=999999999999999999999` / `?limit=99999999999999999999`
    会让 sqlite3 绑参抛 `OverflowError`（Python int 超出 SQLite INTEGER），
    表现为 **HTTP 500**（gk-r27 实测）。带 `le=` 之后变 422，错误在入口就被挡住。
    """
    try:
        return archive_tool.list_archives(
            year=year, kind=kind, problem_code=problem_code, q=q,
            limit=limit, offset=offset, db_path=ARCHIVE_DB)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "error_code": "ARCHIVE_BAD_QUERY", "message": str(exc)}) from exc


@router.get("/archive/summary")
def archive_summary() -> dict:
    """统计（总数 / 按类型 / 按年）+ 文件缺失数 + manifest 存在性。"""
    return archive_tool.archive_summary(db_path=ARCHIVE_DB)


@router.get("/archive/item/{archive_id}")
def archive_item(archive_id: str) -> dict:
    """单条档案详情（库里有、磁盘上可能没文件 —— 详情里不给 `file_present`，
    要看文件在否请走 `/archive/list` 或 `/archive/summary`，那是现算的）。"""
    row = archive_store.get_item(archive_id, db_path=ARCHIVE_DB)
    if row is None:
        raise _not_found(archive_id)
    row["extra"] = archive_tool.parse_extra(row.get("extra"))
    return row


@router.get("/archive/page/{archive_id}/{page_no}")
def archive_page(archive_id: str, page_no: int) -> FileResponse:
    """论文第 `page_no` 页的扫描图（`image/jpeg`）。"""
    try:
        path, media_type = archive_tool.resolve_asset(
            archive_id, page_no=page_no, db_path=ARCHIVE_DB)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "error_code": "ARCHIVE_BAD_PAGE", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        if "档案不存在" in str(exc):
            raise _not_found(archive_id) from exc
        raise HTTPException(status_code=404, detail={
            "error_code": "ARCHIVE_FILE_MISSING", "message": str(exc)}) from exc
    return FileResponse(path, media_type=media_type)


@router.get("/archive/file/{archive_id}")
def archive_file(archive_id: str) -> FileResponse:
    """真题 PDF 原件（`application/pdf`）。"""
    try:
        path, media_type = archive_tool.resolve_asset(archive_id, db_path=ARCHIVE_DB)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={
            "error_code": "ARCHIVE_BAD_PAGE", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        # 档案不存在 → 404 ARCHIVE_NOT_FOUND（与 /archive/item 同一口径）；
        # 档案在、文件丢 → 404 ARCHIVE_FILE_MISSING（两种 404 语义必须可区分）
        if "档案不存在" in str(exc):
            raise _not_found(archive_id) from exc
        raise HTTPException(status_code=404, detail={
            "error_code": "ARCHIVE_FILE_MISSING", "message": str(exc)}) from exc
    return FileResponse(path, media_type=media_type)
