"""RAG 检索路由（FR-4）：`POST /api/v1/rag/search`。

**契约冻结**（README §3，两端照写）：请求 `{"query": str, "k": 5}`；响应
`{"ok", "query", "k", "hits", "index", "latency_ms"}`。

**校验必须在函数体里，不能写进 pydantic validator**：`app.py::_validation_handler` 把
`exc.errors()` 直接 `json.dumps`，而 pydantic v2 的 `ValueError` 会进 `ctx.error`（异常对象）
→ 序列化时 `TypeError` → **500 而不是 422**（D-108.1 同款教训）。故 `query` 只声明类型、
`k` 只声明默认值，空白/越界在函数体判并抛 `422 VALIDATION_ERROR`。

错误码：索引缺失 → `404 RAG_INDEX_MISSING`（message 必须指名 `cli rag rebuild`）；
取向量失败 → `502 RAG_EMBED_FAILED`（`str(exc)` 截断 ≤200 字符，**不落成 500 + 类名**）。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..rag import adapter as rag_adapter

router = APIRouter(prefix="/api/v1", tags=["rag"])

# 契约范围（README §3）：k ∈ [1, 20]，缺省 5
K_MIN = 1
K_MAX = 20
K_DEFAULT = 5
ERROR_MESSAGE_LIMIT = 200


class RagSearchRequest(BaseModel):
    """检索请求体。`query` 必填（空白由路由函数体判 422）；`k` 缺省 5。"""
    query: str
    k: int = K_DEFAULT


def _bad(status: int, code: str, message: str) -> HTTPException:
    """统一错误体入口：`detail` 带 `error_code`，由 `app.py::_http_handler` 泛化分支取用。"""
    return HTTPException(status_code=status,
                         detail={"error_code": code, "message": message})


@router.post("/rag/search")
def rag_search(req: RagSearchRequest) -> dict:
    """在本地向量索引里检索最相关的 k 条真题片段（余弦相似度降序）。"""
    query = (req.query or "").strip()
    if not query:
        raise _bad(422, "VALIDATION_ERROR", "query 不能为空（去空白后长度为 0）")
    if not K_MIN <= req.k <= K_MAX:
        raise _bad(422, "VALIDATION_ERROR",
                   f"k 必须在 {K_MIN}..{K_MAX} 之间，收到 {req.k}")

    start = time.monotonic()
    try:
        result = rag_adapter.search(query, req.k)
    except rag_adapter.IndexMissingError as exc:
        # `IndexMissingError` 的 message 自带「先跑 cli rag rebuild 重建索引」，直接透出
        raise _bad(404, "RAG_INDEX_MISSING", str(exc)) from exc
    except rag_adapter.IndexCorruptError as exc:
        # 索引在但内部不自洽（行数/维度/缺键）：**503 而不是 500**。
        # message 截断 ≤200，但「cli rag rebuild」在前半句，截断不会把修法丢掉。
        raise _bad(503, "RAG_INDEX_CORRUPT", str(exc)[:ERROR_MESSAGE_LIMIT]) from exc
    except rag_adapter.RagEmbedFailed as exc:
        raise _bad(502, "RAG_EMBED_FAILED", str(exc)[:ERROR_MESSAGE_LIMIT]) from exc

    return {
        "ok": True,
        "query": query,
        "k": req.k,
        "hits": result["hits"],
        "index": result["index"],
        "latency_ms": int(round((time.monotonic() - start) * 1000)),
    }
