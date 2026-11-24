"""长任务并发闸（进程内计数，演示级限流）。

上限是**运行期旋钮**，取环境变量 ``MMAGENT_QUEUE_MAX_CONCURRENCY``（默认 2），
**不在** ``config/models.yaml`` 里：该文件是冻结基线，为一个运行期限流去动它要重发基线，
代价远大于收益；环境变量在测试里可直接 monkeypatch。
若日后要配到 ``models.yaml``，需按基线变更流程登记。

规则（见 task-003 §3）：
- 满额时**立刻**抛 429 ``QUEUE_FULL``——不阻塞等待、不排队补跑；
- 额度在 ``finally`` 释放，**异常路径同样释放**；
- 只用 ``/solve/run`` 与 ``/intake/run`` 的执行体，``/run/start``、``/run/resume`` 不套。

不套闸的名单（第 31 轮补登记，**消掉隐性不一致**）：
``/problem/parse``、``/plan/build``、``/paper/build``、``/figures/render`` 属**单步能力**，
不套闸。判据不是「调不调 LLM」而是「**是不是跑整条流水线的执行体**」——``/run/start`` 走完整图
（含 solve / plan / export 节点）都不套，单步能力若套了会与它的口径自相矛盾。
演示级单用户场景下这点并发风险可接受；若日后改为多用户，应统一纳入闸门而不是零散补。

并发语义局限（原样声明，勿润色）：
「进程内额度只对同一进程内的并发请求有效，对多进程/多实例部署无效；这是演示级限流，不是全局限流。」
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException

DEFAULT_MAX_CONCURRENCY = 2
ENV_MAX_CONCURRENCY = "MMAGENT_QUEUE_MAX_CONCURRENCY"

_lock = threading.Lock()
_in_flight = 0


def max_concurrency() -> int:
    """当前上限：环境变量优先；缺失/空串/非整数/非正数一律回退默认 2。"""
    raw = os.environ.get(ENV_MAX_CONCURRENCY, str(DEFAULT_MAX_CONCURRENCY))
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_MAX_CONCURRENCY
    return value if value > 0 else DEFAULT_MAX_CONCURRENCY


def in_flight() -> int:
    """当前占用中的额度数（供测试断言「额度未泄漏」）。"""
    with _lock:
        return _in_flight


def reset() -> None:
    """清零计数（**仅供测试**：避免前序用例残留污染并发断言）。"""
    global _in_flight
    with _lock:
        _in_flight = 0


@contextmanager
def long_task_slot() -> Iterator[None]:
    """占用一个长任务额度；满额立刻 429 `QUEUE_FULL`（detail 带 error_code 供统一错误体取用）。"""
    global _in_flight
    limit = max_concurrency()
    with _lock:
        full = _in_flight >= limit
        if not full:
            _in_flight += 1
    if full:
        raise HTTPException(
            status_code=429,
            detail={"error_code": "QUEUE_FULL",
                    "message": f"长任务并发已达上限（{limit}），请稍后重试"})
    try:
        yield
    finally:
        with _lock:
            _in_flight -= 1
