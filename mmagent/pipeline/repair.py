"""FR-5 修复回路：执行失败后单轮修复脚本。

**依赖方向硬约束**：pipeline 层禁止 import agents，因此「重新生成」由上层以回调
`regen_fn` 注入；本模块只做纯逻辑：拼错误上下文 → 调 regen_fn → guard 校验 → 返回新源码。
"""
from __future__ import annotations

from typing import Callable

from .guard import ScriptGuardError, validate_script_ast

# 注入方约定签名：(prev_src, error_context) -> new_src
RegenFn = Callable[[str, str], str]

MAX_STDERR_CHARS = 2000  # 控制注入给 LLM 的错误上下文长度


class RepairError(Exception):
    """修复失败（轮次用尽或守卫仍不通过）。"""


def build_error_context(stderr_tail: str, guard_error: str = "") -> str:
    """拼装给 LLM 的错误上下文（stderr 尾部 + 守卫违规，截断到上限）。"""
    parts = []
    if guard_error:
        parts.append(f"[静态守卫]\n{guard_error}")
    if stderr_tail:
        parts.append(f"[执行 stderr 尾部]\n{stderr_tail}")
    ctx = "\n\n".join(parts) or "（无可用错误信息）"
    return ctx[:MAX_STDERR_CHARS]


def repair_once(prev_src: str, stderr_tail: str, regen_fn: RegenFn,
                max_rounds: int = 1) -> str:
    """单轮（可配置多轮）修复：返回通过守卫的新源码。

    每轮：拼错误上下文 → regen_fn(prev_src, context) → validate_script_ast。
    守卫不过时把违规并入上下文进入下一轮；轮次用尽抛 RepairError。
    注意：本函数不执行脚本（执行由调用方用 runner.run_script 负责）。

    `max_rounds <= 0` 表示**未请求任何修复轮**：本函数**不调用 regen_fn**，
    直接抛 `RepairError`（task-005 修正 N1：旧实现用 `max(1, max_rounds)` 把 0 抬成 1，
    导致 `repair_rounds=0` 仍执行 1 轮修复，把首轮失败掩盖成成功）。
    """
    if not callable(regen_fn):
        raise TypeError("regen_fn 必须是可调用对象（由上层注入，如 agents.coder 的包装）")

    rounds = int(max_rounds)
    if rounds <= 0:
        raise RepairError(
            "max_rounds<=0：未请求任何修复轮（调用方应在 repair_rounds<=0 时跳过修复分支）")

    guard_error = ""
    last_src = prev_src
    last_error = stderr_tail or "（未提供 stderr）"
    for _ in range(1, rounds + 1):
        context = build_error_context(last_error, guard_error)
        new_src = regen_fn(last_src, context)
        if not isinstance(new_src, str) or not new_src.strip():
            guard_error = "regen_fn 返回空源码"
            continue
        try:
            validate_script_ast(new_src)
            return new_src
        except ScriptGuardError as exc:
            guard_error = str(exc)
            last_src, last_error = new_src, guard_error
    raise RepairError(
        f"修复 {rounds} 轮后仍不可用：{guard_error or '守卫未通过'}")
