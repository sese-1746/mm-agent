"""agents 层：LLM 角色与协调器。只允许调用 tools/pipeline/store，不被下层依赖。

`coordinator` 自 task-002 起为 LangGraph 人机回环实现（`StateGraph` + `interrupt()`
+ `SqliteSaver`），在此 re-export 其对外接口，便于 `from mmagent.agents import Coordinator`。
"""
from .coordinator import (
    Coordinator,
    CoordinatorGraphState,
    CoordinatorState,
    CheckpointUnavailable,
    DEFAULT_CKPT_PATH,
    GATE_CALIBER,
    GATE_COMPLIANCE,
    GATE_SANITY,
    HumanGateRequired,
    InvalidGateDecision,
    MAX_RERUN,
    RunStatus,
    Stage,
    UnknownRunError,
)

__all__ = [
    "Coordinator",
    "CoordinatorGraphState",
    "CoordinatorState",
    "CheckpointUnavailable",
    "DEFAULT_CKPT_PATH",
    "GATE_CALIBER",
    "GATE_COMPLIANCE",
    "GATE_SANITY",
    "HumanGateRequired",
    "InvalidGateDecision",
    "MAX_RERUN",
    "RunStatus",
    "Stage",
    "UnknownRunError",
]
