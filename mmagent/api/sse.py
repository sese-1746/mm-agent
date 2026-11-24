"""SSE 流式协作过程（规划书 §7.1 `POST /api/v1/stream/run`）。

**数据边界（R6，不许越界）**：事件源只有 `events` 表（6 列
`id/ts/event_type/actor/payload/trace_id`，**无 run_id / stage / artifact_id 列**）。
因此：

| 键 | 来源 | 缺失时 |
|---|---|---|
| `seq` | `events.id` | 必有 |
| `stage` | `payload["stage"]`，其次 `payload["to"]` | `null` |
| `state` | `STATE_BY_EVENT_TYPE[event_type]` | `null` |
| `message` | `payload["question"]/["message"]/["reason"]`，都没有 → `event_type` | 必有 |
| `artifact_id` | **恒 `null`**（全仓无来源，禁止伪造） | `null` |

**不做的事**：七阶段流水线（`readin/plan/code/plot/paper/review`）在本仓库**全是 stub**，
events 里没有逐阶段进度，`coordinator.transition` 只是每次 `invoke()` 后的一条整体跃迁。
本模块**不编造**任何阶段名或进度。

分层：本模块只做序列化与轮询生成器，**不裸写 SQL**（查询在 `store/audit.py`）。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..store import audit as audit_store
from ..store.db import DEFAULT_DB_PATH

router = APIRouter(prefix="/api/v1", tags=["stream"])

# 轮询间隔与请求参数上限（防被用来拖死服务）
POLL_INTERVAL_S = 0.2
MAX_TIMEOUT_S = 60.0
MAX_EVENTS_CAP = 1000

# `state` 派生表：**只按 events.event_type 的真实取值映射**，未列出的一律 `null`。
# 真实取值来自生产库对 event_type 的 DISTINCT 全量取样：
#   coordinator.transition / hitl.gate_paused / hitl.run_started /
#   hitl.gate_resumed / hitl.run_finished
# `state` 在 events 里**无列、payload 也无统一键**，故这是派生语义，不是读出来的原值。
STATE_BY_EVENT_TYPE = {
    "coordinator.transition": "running",
    "hitl.gate_paused": "paused",
}

# 事件体**严格五键**（多一键/少一键都是契约违规）
EVENT_KEYS = ("seq", "stage", "state", "message", "artifact_id")


class StreamRunRequest(BaseModel):
    """SSE 请求体：`run_id` 必填；`timeout_s`/`max_events` 是**必须**的终止条件。"""
    run_id: str
    timeout_s: float = 10.0
    max_events: int = 200


def _clamp_timeout(raw: float) -> float:
    """`timeout_s` 夹到 `[0, 60]`（负值 → 0，立即结束）。"""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 10.0
    return max(0.0, min(val, MAX_TIMEOUT_S))


def _clamp_max_events(raw: int) -> int:
    """`max_events` 夹到 `[0, 1000]`。"""
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return 200
    return max(0, min(val, MAX_EVENTS_CAP))


def _text(payload: dict, *keys: str) -> str | None:
    """按顺序取第一个非空字符串值；都没有 → `None`（**不填固定值**）。"""
    for key in keys:
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _load_payload(raw: str) -> dict:
    """`events.payload` 是 JSON 文本；解析失败按空对象处理（字段走缺失分支 → null）。"""
    try:
        obj = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def event_body(row: dict) -> dict:
    """把一行 `events` 映射成**严格五键**的 SSE 事件体。"""
    payload = _load_payload(row.get("payload") or "")
    event_type = str(row.get("event_type") or "")
    return {
        "seq": row.get("id"),
        "stage": _text(payload, "stage", "to"),
        "state": STATE_BY_EVENT_TYPE.get(event_type),
        "message": _text(payload, "question", "message", "reason") or event_type,
        "artifact_id": None,
    }


async def _stream(run_id: str, timeout_s: float, max_events: int,
                  db_path: str | Path):
    """按 `id > cursor` 增量轮询事件表并 yield SSE 帧；到时/到量即收尾。

    终止条件**三重**：`max_events` 用尽、`timeout_s` 用尽、或事件源不再有新行且已到时。
    收尾帧固定为 `event: end` + `data: {"events": <已推条数>}`。
    """
    cursor = 0
    sent = 0
    deadline = time.monotonic() + timeout_s
    while sent < max_events:
        rows = audit_store.list_events_since(
            after_id=cursor, run_id=run_id, limit=max_events - sent, db_path=db_path)
        for row in rows:
            if sent >= max_events:
                break
            cursor = int(row["id"])
            sent += 1
            body = event_body(row)
            yield f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
        if sent >= max_events:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(POLL_INTERVAL_S, remaining))
    yield f"event: end\ndata: {json.dumps({'events': sent})}\n\n"


@router.post("/stream/run")
def stream_run(req: StreamRunRequest, request: Request) -> StreamingResponse:
    """SSE 推流：`run_id` 只用于 `json_extract(payload,'$.run_id')` 过滤。

    事件字段全部来自 `events` 的既有内容；`artifact_id` 恒 `null`（无来源）。
    库路径优先级：`app.state.default_db`（`cli serve --db`）→ 模块默认值。
    """
    db_path = getattr(request.app.state, "default_db", None) or DEFAULT_DB_PATH
    return StreamingResponse(
        _stream(req.run_id, _clamp_timeout(req.timeout_s),
                _clamp_max_events(req.max_events), db_path),
        media_type="text/event-stream")
