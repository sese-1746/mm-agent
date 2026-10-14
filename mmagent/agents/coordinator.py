"""coordinator：规则状态机骨架（本轮纯 Python，无 LangGraph 依赖）。

阶段：INTAKE → PROFILE → PLAN → SOLVE → RENDER → ASSEMBLE → EXPORT
三个人工卡点（对应后续 LangGraph interrupt()）：
1. 口径冲突未确认（INTAKE 后）  2. sanity blocking（SOLVE 后）  3. 导出前合规确认（EXPORT 前）
失败规则：同一阶段连续失败 2 次 → 转人工（HumanGateRequired）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

MAX_CONSECUTIVE_FAILS = 2  # 连续失败 2 次转人工


class Stage(str, Enum):
    INTAKE = "INTAKE"
    PROFILE = "PROFILE"
    PLAN = "PLAN"
    SOLVE = "SOLVE"
    RENDER = "RENDER"
    ASSEMBLE = "ASSEMBLE"
    EXPORT = "EXPORT"
    DONE = "DONE"
    HALTED = "HALTED"  # 人工卡点挂起


class HumanGateRequired(Exception):
    """人工卡点：流程须挂起等待人工确认（对应 LangGraph interrupt() 占位）。"""

    def __init__(self, gate: str, detail: str = ""):
        self.gate = gate
        super().__init__(f"人工卡点[{gate}]：{detail or '等待人工确认'}")


# 阶段转移表：当前阶段 → 下一阶段
TRANSITIONS: dict[Stage, Stage] = {
    Stage.INTAKE: Stage.PROFILE,
    Stage.PROFILE: Stage.PLAN,
    Stage.PLAN: Stage.SOLVE,
    Stage.SOLVE: Stage.RENDER,
    Stage.RENDER: Stage.ASSEMBLE,
    Stage.ASSEMBLE: Stage.EXPORT,
    Stage.EXPORT: Stage.DONE,
}


@dataclass
class CoordinatorState:
    """协调器状态：context 携带卡点判定位与最近错误。"""
    stage: Stage = Stage.INTAKE
    fail_counts: dict = field(default_factory=dict)   # stage -> 连续失败次数
    context: dict = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class Coordinator:
    """纯 Python 路由：step(state) 返回下一状态，或抛 HumanGateRequired 挂起。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def _emit(self, state: CoordinatorState, event_type: str, payload: dict) -> None:
        try:
            conn = connect(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO events (ts, event_type, actor, payload, trace_id)"
                    " VALUES (?,?,?,?,?)",
                    (utcnow(), event_type, "coordinator",
                     json.dumps(payload, ensure_ascii=False), state.trace_id))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # 观测失败不阻断路由

    def record_failure(self, state: CoordinatorState, error: str) -> CoordinatorState:
        """记录当前阶段失败；连续失败达阈值 → 转人工。"""
        key = state.stage.value
        state.fail_counts[key] = state.fail_counts.get(key, 0) + 1
        state.context["last_error"] = error
        self._emit(state, "coordinator.stage_failed",
                   {"stage": key, "count": state.fail_counts[key], "error": error})
        if state.fail_counts[key] >= MAX_CONSECUTIVE_FAILS:
            state.stage = Stage.HALTED
            raise HumanGateRequired(
                "consecutive_failures",
                f"阶段 {key} 连续失败 {state.fail_counts[key]} 次：{error}")
        return state

    def record_success(self, state: CoordinatorState) -> None:
        state.fail_counts[state.stage.value] = 0
        state.context.pop("last_error", None)

    def step(self, state: CoordinatorState) -> CoordinatorState:
        """推进一个阶段：先查人工卡点，再按转移表路由。"""
        ctx = state.context

        # 卡点 1：口径冲突未确认（INTAKE → PROFILE 前）
        # TODO(LangGraph): 此处替换为 interrupt()，由人工在 UI 确认后恢复
        if state.stage == Stage.INTAKE and ctx.get("caliber_conflicts") \
                and not ctx.get("conflicts_confirmed"):
            state.stage = Stage.HALTED
            raise HumanGateRequired("caliber_conflict",
                                    "存在口径冲突，需人工确认 field_mapping 后继续")
        # 卡点 2：sanity blocking（SOLVE → RENDER 前）
        # TODO(LangGraph): interrupt()
        if state.stage == Stage.SOLVE and ctx.get("sanity_blocking"):
            state.stage = Stage.HALTED
            raise HumanGateRequired("sanity_blocking",
                                    "理智性检查存在阻断项，需人工裁决")
        # 卡点 3：导出前合规确认（ASSEMBLE → EXPORT 前）
        # TODO(LangGraph): interrupt()
        if state.stage == Stage.ASSEMBLE and not ctx.get("compliance_confirmed"):
            state.stage = Stage.HALTED
            raise HumanGateRequired("pre_export_compliance",
                                    "导出前需人工确认 AI 使用披露与合规声明")

        nxt = TRANSITIONS.get(state.stage)
        if nxt is None:
            raise HumanGateRequired("halted", f"阶段 {state.stage.value} 不可自动推进")
        self._emit(state, "coordinator.transition",
                   {"from": state.stage.value, "to": nxt.value})
        state.stage = nxt
        return state
