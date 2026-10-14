"""coordinator：LangGraph `StateGraph` 人机回环（HITL）协调器。

本模块把 task-001 的纯 Python 状态机改造为 **LangGraph StateGraph + `interrupt()`
+ `SqliteSaver`**，让三个人工卡点可暂停、可**跨进程**恢复（NFR-5）。

## 两条人工介入路径的分工（重要）

- **三个人工卡点**（口径冲突 / 合理性检查 blocking / 导出前合规）走 LangGraph
  `interrupt()` 暂停 + `Command(resume=...)` 恢复，对外以 `RunStatus` 返回，
  检查点落独立 SQLite 库（默认 ``data/checkpoints.db``），支持跨进程续跑。
- **`HumanGateRequired`** 只用于「**同一阶段连续失败 `MAX_CONSECUTIVE_FAILS` 次
  → 转人工**」这条升级路径（见 `record_failure()`），**不再**用于上面三个 Graph 卡点。
- `Stage`、`MAX_CONSECUTIVE_FAILS`、`CoordinatorState` 作为共享词表保留。

## 图结构（条件路由 + halt + 回边，修复轮 2）

    START → intake → gate_caliber ─(→profile)→ profile → plan → solve → gate_sanity
          ├─ accept ────────────────────────────────────────────────→ render → assemble
          │                                                              → gate_compliance
          ├─ fix_and_rerun ─(rerun_solve, 最多 MAX_RERUN 次)→ solve
          └─ abort ─────────────────────────────────────────────────→ halt → END
    gate_compliance ├─ approve → export → END
                    └─ reject  → halt  → END

- 三条卡点出边用 **`add_conditional_edges`**：裁定**驱动控制流**（不再"只记录不消费"）。
- **`halt`** 是正常终态：`stage="HALTED"`、`halted=True`、`done=False`。
- **回边上限**：`fix_and_rerun` 重入 `solve` 最多 `MAX_RERUN`(=1) 次，超限 → `halt`
  （依据课件 `07_Graph_Workflow 架构` P10 L786：条件路由设计缺陷会造成无限循环，必须给阈值）。

## 语义契约（冻结）

- **`done=True` 仅当 `export` 节点跑完**（`stage="DONE"`）；`HALTED` 也是**正常终态**，非异常。
- 卡点 `interrupt()` 的 payload 恒含 `gate` / `stage` / `question` / **`review`** 四键；
  `review` 携带该审的待审材料（无材料时为空容器，**绝不为 `None`**）。
- `resume()` 任何非法裁定（None / 空 dict / 非 dict / 缺键 / 越界 / 投错 gate / 快照过期）
  在调用 langgraph **之前**抛 `InvalidGateDecision`。

## 硬约束（来自 task-002 调查，违反会踩坑）

1. 恢复**只能**用 ``Command(resume=X)``；``invoke(None, cfg)`` 不会恢复。
2. **永不**传 ``resume=None``（langgraph 1.2.15 会抛 ``UnboundLocalError``），
   也**永不**传空 dict ``{}``（``Command(resume={})`` 会静默不恢复）。
3. `interrupt()` **之前**的代码在每次恢复时会重跑，因此卡点节点内**不得**有
   非幂等写操作；`events` 留痕一律由 **driver**（`start()`/`resume()`）写，
   不在节点内写（含 `hitl.run_halted`）。
4. 一个节点只放一个卡点（同节点多 `interrupt()` 会让节点执行 N+1 次）。
"""
from __future__ import annotations

import json
import operator
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated

from typing_extensions import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

# 检查点默认落在**独立**库：data/*.db 已在 .gitignore 内（天然不被提交），
# 且与业务库 data/mm.db 隔离（避免污染 profile_doc 不可变触发器 / 写锁竞争）。
DEFAULT_CKPT_PATH = Path("data/checkpoints.db")

MAX_CONSECUTIVE_FAILS = 2  # 连续失败 2 次转人工
MAX_RERUN = 1              # fix_and_rerun 重入 solve 的最大次数（防无限循环）


class Stage(str, Enum):
    INTAKE = "INTAKE"
    PROFILE = "PROFILE"
    PLAN = "PLAN"
    SOLVE = "SOLVE"
    RENDER = "RENDER"
    ASSEMBLE = "ASSEMBLE"
    EXPORT = "EXPORT"
    DONE = "DONE"
    HALTED = "HALTED"  # 人工否决/中止，或同阶段连续失败达阈值 → 正常终态（非异常）


# ---------------------------------------------------------------- 卡点契约（冻结）

GATE_CALIBER = "caliber_conflict"
GATE_SANITY = "sanity_blocking"
GATE_COMPLIANCE = "pre_export_compliance"

# gate -> (必需取值键, 允许取值域)。resume 值形如 {"<key>": <域内值>, "note": <str>}。
GATE_DECISION_SPEC: dict[str, tuple[str, frozenset[str]]] = {
    GATE_CALIBER: ("resolution", frozenset({"use_mapping", "keep_raw", "human_override"})),
    GATE_SANITY: ("decision", frozenset({"accept", "fix_and_rerun", "abort"})),
    GATE_COMPLIANCE: ("decision", frozenset({"approve", "reject"})),
}

# 各卡点 payload 里的 stage（= interrupt 发生时所处阶段）
GATE_STAGE: dict[str, str] = {
    GATE_CALIBER: Stage.INTAKE.value,
    GATE_SANITY: Stage.SOLVE.value,
    GATE_COMPLIANCE: Stage.ASSEMBLE.value,
}

GATE_QUESTION: dict[str, str] = {
    GATE_CALIBER: "存在口径冲突，需人工确认 field_mapping 建议后继续",
    GATE_SANITY: "合理性检查存在阻断项，需人工裁决后继续",
    GATE_COMPLIANCE: "导出前需人工确认 AI 使用披露与合规声明",
}

# 各卡点待审材料在 context/state ``materials`` 里的键（review 的来源）
GATE_REVIEW_KEY: dict[str, str] = {
    GATE_CALIBER: "conflicts",           # 口径冲突明细
    GATE_SANITY: "sanity_items",         # 阻断项明细
    GATE_COMPLIANCE: "missing_disclosure",  # 缺失披露项
}


class HumanGateRequired(Exception):
    """同阶段连续失败达阈值的**升级路径**：流程转人工。

    注意：三个 Graph 卡点（口径冲突 / sanity blocking / 导出前合规）走
    `interrupt()` + `RunStatus`，**不抛**本异常。
    """

    def __init__(self, gate: str, detail: str = ""):
        self.gate = gate
        super().__init__(f"人工卡点[{gate}]：{detail or '等待人工确认'}")


class InvalidGateDecision(Exception):
    """对外 `resume()` 收到非法人工裁定（None / 空 dict / 非 dict / 缺键 / 越界 /
    投错 gate / 线程未挂起 / 挂起快照已过期）。"""

    def __init__(self, gate: str, reason: str):
        self.gate = gate
        self.reason = reason
        super().__init__(f"非法人工裁定[{gate}]：{reason}")


class UnknownRunError(Exception):
    """`status()` 查询的 run_id（thread）不存在。"""

    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"未知 run_id：{run_id!r}（该线程没有任何检查点）")


class CheckpointUnavailable(Exception):
    """检查点库不可用（只读 / 加锁失败等 `sqlite3.OperationalError`）。"""


# 阶段转移表：当前阶段 → 下一阶段（共享词表，保留）
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
    """协调器状态（共享词表，保留）：context 携带卡点判定位与最近错误。"""
    stage: Stage = Stage.INTAKE
    fail_counts: dict = field(default_factory=dict)   # stage -> 连续失败次数
    context: dict = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)


# ---------------------------------------------------------------- Graph 状态 schema

class CoordinatorGraphState(TypedDict):
    """LangGraph 图状态（TypedDict）。

    - `log` 用 ``operator.add`` 累加语义；
    - `decisions` 为普通 dict（覆盖语义），卡点节点返回时自带 **合并后的完整
      dict**，以累积多个卡点的裁定；
    - `materials` 为 start() 传入的待审材料（用 `gate -> review` 填充 interrupt payload）；
    - `rerun_count` 记录 `fix_and_rerun` 已重入 solve 的次数（上限 `MAX_RERUN`）；
    - `sanity_resolved` 标记 sanity 卡点本轮是否已拿到裁定（重跑后复位，以便再次评审）；
    - 无 `messages`（本项目不需要）。
    """
    stage: str
    caliber_conflicts: bool
    conflicts_confirmed: bool
    sanity_blocking: bool
    compliance_confirmed: bool
    decisions: dict
    materials: dict
    rerun_count: int
    sanity_resolved: bool
    halted_gate: str | None
    halted_decision: dict
    log: Annotated[list, operator.add]


def _review(state: CoordinatorGraphState, gate: str):
    """取该卡点的待审材料；无材料时返回**空容器**（绝不 None）。"""
    materials = state.get("materials") or {}
    value = materials.get(GATE_REVIEW_KEY[gate])
    if value is None:
        return {}
    return value


# ---------------------------------------------------------------- 节点（阶段节点 + 卡点节点 + halt/rerun）

def _node_intake(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.INTAKE.value, "log": ["stage:intake"]}


def _node_gate_caliber(state: CoordinatorGraphState) -> dict:
    """卡点 1：口径冲突未确认 → interrupt。（裁定只描述"如何解决"，合法裁定后一律继续）"""
    if state.get("caliber_conflicts") and not state.get("conflicts_confirmed"):
        decision = interrupt({
            "gate": GATE_CALIBER,
            "stage": GATE_STAGE[GATE_CALIBER],
            "question": GATE_QUESTION[GATE_CALIBER],
            "review": _review(state, GATE_CALIBER),
        })
        return {
            "conflicts_confirmed": True,
            "decisions": {**state.get("decisions", {}), GATE_CALIBER: decision},
            "log": [f"gate:{GATE_CALIBER}={decision.get('resolution')}"],
        }
    return {"log": [f"gate:{GATE_CALIBER}=skip"]}


def _node_profile(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.PROFILE.value, "log": ["stage:profile"]}


def _node_plan(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.PLAN.value, "log": ["stage:plan"]}


def _node_solve(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.SOLVE.value, "log": ["stage:solve"]}


def _node_gate_sanity(state: CoordinatorGraphState) -> dict:
    """卡点 2：sanity blocking → interrupt。裁定驱动去向（accept/abort/fix_and_rerun）。"""
    if state.get("sanity_blocking") and not state.get("sanity_resolved"):
        decision = interrupt({
            "gate": GATE_SANITY,
            "stage": GATE_STAGE[GATE_SANITY],
            "question": GATE_QUESTION[GATE_SANITY],
            "review": _review(state, GATE_SANITY),
        })
        return {
            "sanity_resolved": True,
            "decisions": {**state.get("decisions", {}), GATE_SANITY: decision},
            "log": [f"gate:{GATE_SANITY}={decision.get('decision')}"],
        }
    return {"log": [f"gate:{GATE_SANITY}=skip"]}


def _node_rerun_solve(state: CoordinatorGraphState) -> dict:
    """`fix_and_rerun` 回边：计数 +1，复位 sanity 卡点以便重跑后**再次评审**。"""
    return {
        "rerun_count": int(state.get("rerun_count", 0)) + 1,
        "sanity_resolved": False,
        "log": ["rerun:solve"],
    }


def _node_render(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.RENDER.value, "log": ["stage:render"]}


def _node_assemble(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.ASSEMBLE.value, "log": ["stage:assemble"]}


def _node_gate_compliance(state: CoordinatorGraphState) -> dict:
    """卡点 3：导出前合规未确认 → interrupt。approve→export，reject→halt。"""
    if not state.get("compliance_confirmed"):
        decision = interrupt({
            "gate": GATE_COMPLIANCE,
            "stage": GATE_STAGE[GATE_COMPLIANCE],
            "question": GATE_QUESTION[GATE_COMPLIANCE],
            "review": _review(state, GATE_COMPLIANCE),
        })
        return {
            "compliance_confirmed": True,
            "decisions": {**state.get("decisions", {}), GATE_COMPLIANCE: decision},
            "log": [f"gate:{GATE_COMPLIANCE}={decision.get('decision')}"],
        }
    return {"log": [f"gate:{GATE_COMPLIANCE}=skip"]}


def _node_export(state: CoordinatorGraphState) -> dict:
    return {"stage": Stage.DONE.value, "log": ["stage:export"]}


def _node_halt(state: CoordinatorGraphState) -> dict:
    """正常终态：`stage=HALTED`。记录是哪条裁定触发的 halt（供 driver 写事件）。"""
    decisions = state.get("decisions") or {}
    halted_gate = None
    for gate in (GATE_COMPLIANCE, GATE_SANITY):
        decision = decisions.get(gate) or {}
        value = decision.get("decision")
        if gate == GATE_COMPLIANCE and value == "reject":
            halted_gate = gate
            break
        if gate == GATE_SANITY and value in ("abort", "fix_and_rerun"):
            halted_gate = gate
            break
    return {
        "stage": Stage.HALTED.value,
        "halted_gate": halted_gate,
        "halted_decision": dict(decisions.get(halted_gate) or {}) if halted_gate else {},
        "log": ["stage:halt"],
    }


# ---------------------------------------------------------------- 条件路由（裁定驱动控制流）

def _route_caliber(state: CoordinatorGraphState) -> str:
    """口径冲突只有"如何解决"语义 → 合法裁定后一律继续到 profile。"""
    return "profile"


def _route_sanity(state: CoordinatorGraphState) -> str:
    """sanity 裁定 → 去向。卡点被 skip（sanity_blocking=False）时无裁定 → 放行 render。"""
    decision = (state.get("decisions", {}).get(GATE_SANITY) or {}).get("decision")
    if decision in (None, "accept"):
        return "render"
    if decision == "fix_and_rerun":
        # 回边上限：超过 MAX_RERUN 次重入 solve → halt（防无限循环）
        if int(state.get("rerun_count", 0)) >= MAX_RERUN:
            return "halt"
        return "rerun_solve"
    # abort → halt
    return "halt"


def _route_compliance(state: CoordinatorGraphState) -> str:
    """合规裁定 → 去向。卡点被 skip（compliance_confirmed=True）时无裁定 → 放行 export。"""
    decision = (state.get("decisions", {}).get(GATE_COMPLIANCE) or {}).get("decision")
    return "halt" if decision == "reject" else "export"


def _build_graph(checkpointer):
    """编译图（条件路由 + halt + fix_and_rerun 回边）。"""
    builder = StateGraph(CoordinatorGraphState)
    builder.add_node("intake", _node_intake)
    builder.add_node("gate_caliber", _node_gate_caliber)
    builder.add_node("profile", _node_profile)
    builder.add_node("plan", _node_plan)
    builder.add_node("solve", _node_solve)
    builder.add_node("gate_sanity", _node_gate_sanity)
    builder.add_node("rerun_solve", _node_rerun_solve)
    builder.add_node("render", _node_render)
    builder.add_node("assemble", _node_assemble)
    builder.add_node("gate_compliance", _node_gate_compliance)
    builder.add_node("export", _node_export)
    builder.add_node("halt", _node_halt)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "gate_caliber")
    builder.add_conditional_edges("gate_caliber", _route_caliber, {"profile": "profile"})
    builder.add_edge("profile", "plan")
    builder.add_edge("plan", "solve")
    builder.add_edge("solve", "gate_sanity")
    builder.add_conditional_edges("gate_sanity", _route_sanity, {
        "render": "render",
        "rerun_solve": "rerun_solve",
        "halt": "halt",
    })
    builder.add_edge("rerun_solve", "solve")
    builder.add_edge("render", "assemble")
    builder.add_edge("assemble", "gate_compliance")
    builder.add_conditional_edges("gate_compliance", _route_compliance, {
        "export": "export",
        "halt": "halt",
    })
    builder.add_edge("export", END)
    builder.add_edge("halt", END)
    return builder.compile(checkpointer=checkpointer)


def _open_ckpt_conn(path: Path) -> sqlite3.Connection:
    """打开检查点库连接。

    **不复用** ``store.db.connect()``：它未设 ``check_same_thread=False``，
    而 langgraph 在 worker 线程里写检查点，会抛 ``ProgrammingError``
    （实测见 ``probe/probe_11_max_conn_compat.py``）。这里手工复刻其
    ``busy_timeout`` 思路并补 ``check_same_thread=False``。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA journal_mode=WAL;")  # 库级持久设置，打开时设一次即可
    return conn


# ---------------------------------------------------------------- 对外状态对象

@dataclass
class RunStatus:
    """一次运行（= 一个 langgraph thread）的对外快照。"""
    run_id: str
    stage: str
    paused: bool                       # True=停在人工卡点
    gate: str | None = None            # paused=True 时必有
    question: str | None = None
    done: bool = False
    log: list = field(default_factory=list)
    halted: bool = False               # True=已按裁定中止（正常终态，stage=HALTED）
    review: dict | list | None = None  # paused=True 时的待审材料（无材料时为空容器）

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "paused": self.paused,
            "gate": self.gate,
            "question": self.question,
            "done": self.done,
            "halted": self.halted,
            "review": self.review,
            "log": list(self.log),
        }


# ---------------------------------------------------------------- driver

class Coordinator:
    """人机回环协调器（LangGraph StateGraph + SqliteSaver）。

    - `thread_id` = 调用方传入的 `run_id`（稳定、可持久化，跨进程恢复键）；
    - 检查点落 `ckpt_path`（独立库）；
    - `events` 留痕一律由本类（driver）写，卡点节点内不写。
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH,
                 ckpt_path: str | Path = DEFAULT_CKPT_PATH):
        self.db_path = db_path
        self.ckpt_path = Path(ckpt_path)
        self._emit_degraded = False  # 留痕降级告警去重标志（首次失败后置 True）
        try:
            self._conn = _open_ckpt_conn(self.ckpt_path)
        except sqlite3.Error as exc:
            raise CheckpointUnavailable(
                f"无法打开检查点库 {self.ckpt_path}（{exc}）；请检查路径是否可写") from exc
        self._checkpointer = SqliteSaver(self._conn)
        self._graph = _build_graph(self._checkpointer)

    # ---- 内部工具 ----

    def _config(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def _emit(self, event_type: str, payload: dict, trace_id: str) -> None:
        """写一条 events 留痕（沿用既有 _emit 风格）。

        **观测失败不阻断流程**（保持原意图），但**绝不静默**：首次写入失败会往
        stderr 打一条明确警告（含真实异常与修复提示），并用 `_emit_degraded`
        抑制后续重复刷屏。
        """
        try:
            conn = connect(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO events (ts, event_type, actor, payload, trace_id)"
                    " VALUES (?,?,?,?,?)",
                    (utcnow(), event_type, "coordinator",
                     json.dumps(payload, ensure_ascii=False), trace_id))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            if not self._emit_degraded:
                self._emit_degraded = True
                print(
                    f"[coordinator] 事件留痕失败，已降级（后续同类错误不再重复提示）："
                    f"{type(exc).__name__}: {exc}；"
                    f"请先执行 `python -m mmagent.cli db init --db {self.db_path}`",
                    file=sys.stderr)

    def _snapshot(self, run_id: str):
        """取当前 thread 的最新检查点快照（存储异常转 CheckpointUnavailable）。"""
        try:
            return self._graph.get_state(self._config(run_id))
        except sqlite3.OperationalError as exc:
            raise CheckpointUnavailable(
                f"检查点库读取失败（{exc}）；请检查 {self.ckpt_path} 是否可读、是否被独占") from exc

    @staticmethod
    def _status_from(run_id: str, snap) -> RunStatus:
        values = dict(snap.values or {})
        stage = values.get("stage", "") or ""
        log = list(values.get("log", []))
        paused = False
        gate = None
        question = None
        review = None
        for task in (snap.tasks or ()):
            for iv in (task.interrupts or ()):
                payload = iv.value if isinstance(iv.value, dict) else {}
                gate = payload.get("gate")
                question = payload.get("question")
                review = payload.get("review")
                if payload.get("stage"):
                    stage = payload["stage"]
                paused = True
                break
            if paused:
                break
        done = (not paused) and stage == Stage.DONE.value
        halted = (not paused) and stage == Stage.HALTED.value
        return RunStatus(run_id=run_id, stage=stage, paused=paused, gate=gate,
                         question=question, done=done, log=log, halted=halted,
                         review=review)

    def _read_status(self, run_id: str) -> RunStatus:
        return self._status_from(run_id, self._snapshot(run_id))

    def _after_invoke(self, run_id: str, trace_id: str, from_stage: str) -> RunStatus:
        """invoke 之后统一收尾：写 transition / paused / halted / finished 留痕。

        注意：本方法只在 driver 调用一次，故「同一卡点只留 1 条 hitl.gate_paused」。
        """
        snap = self._snapshot(run_id)
        status = self._status_from(run_id, snap)
        self._emit("coordinator.transition",
                   {"run_id": run_id, "from": from_stage, "to": status.stage}, trace_id)
        if status.paused:
            self._emit("hitl.gate_paused",
                       {"run_id": run_id, "gate": status.gate, "stage": status.stage,
                        "question": status.question}, trace_id)
        elif status.halted:
            values = dict(snap.values or {})
            self._emit("hitl.run_halted",
                       {"run_id": run_id, "gate": values.get("halted_gate"),
                        "decision": values.get("halted_decision"), "stage": status.stage},
                       trace_id)
        elif status.done:
            self._emit("hitl.run_finished",
                       {"run_id": run_id, "stage": status.stage}, trace_id)
        return status

    def _invoke(self, payload, config) -> None:
        """调用 langgraph，把 sqlite3.OperationalError 转成受控的 CheckpointUnavailable。"""
        try:
            self._graph.invoke(payload, config=config)
        except sqlite3.OperationalError as exc:
            raise CheckpointUnavailable(
                f"检查点库不可用（{exc}）；请检查 {self.ckpt_path} 是否可写、是否被独占") from exc

    # ---- 输入校验（在调用 Command 之前做）----

    @staticmethod
    def _validate_decision(gate: str, decision) -> None:
        """校验人工裁定；不合法抛 InvalidGateDecision，绝不把非法值传给 langgraph。"""
        if decision is None:
            raise InvalidGateDecision(
                gate, "decision 不得为 None（langgraph 1.2.15 对 resume=None 抛 UnboundLocalError）")
        if not isinstance(decision, dict):
            raise InvalidGateDecision(
                gate, f"decision 必须是 dict，实际 {type(decision).__name__}")
        if not decision:
            raise InvalidGateDecision(
                gate, "decision 不得为空 dict（Command(resume={}) 会静默不恢复）")
        spec = GATE_DECISION_SPEC.get(gate)
        if spec is None:
            raise InvalidGateDecision(
                gate, f"未知卡点名 {gate!r}（合法：{sorted(GATE_DECISION_SPEC)}）")
        key, allowed = spec
        if key not in decision:
            raise InvalidGateDecision(gate, f"缺必需键 {key!r}")
        if decision[key] not in allowed:
            raise InvalidGateDecision(
                gate, f"{key}={decision[key]!r} 越界（合法：{sorted(allowed)}）")
        if "note" not in decision:
            raise InvalidGateDecision(gate, "缺必需键 'note'")
        if not isinstance(decision["note"], str):
            raise InvalidGateDecision(gate, "'note' 必须是 str")

    # ---- 对外接口 ----

    def start(self, run_id: str, context: dict) -> RunStatus:
        """启动一次运行：首次推演，停在卡点、跑到 DONE、或按裁定 halt。"""
        if not isinstance(context, dict):
            raise InvalidGateDecision("<start>", f"context 必须是 dict，实际 {type(context).__name__}")
        materials = {
            GATE_REVIEW_KEY[GATE_CALIBER]: context.get("conflicts"),
            GATE_REVIEW_KEY[GATE_SANITY]: context.get("sanity_items"),
            GATE_REVIEW_KEY[GATE_COMPLIANCE]: context.get("missing_disclosure"),
        }
        initial: CoordinatorGraphState = {
            "stage": Stage.INTAKE.value,
            "caliber_conflicts": bool(context.get("caliber_conflicts", False)),
            "conflicts_confirmed": bool(context.get("conflicts_confirmed", False)),
            "sanity_blocking": bool(context.get("sanity_blocking", False)),
            "compliance_confirmed": bool(context.get("compliance_confirmed", False)),
            "decisions": {},
            "materials": materials,
            "rerun_count": 0,
            "sanity_resolved": False,
            "halted_gate": None,
            "halted_decision": {},
            "log": [],
        }
        trace = uuid.uuid4().hex
        self._emit("hitl.run_started",
                   {"run_id": run_id, "context": {k: initial[k] for k in
                    ("caliber_conflicts", "conflicts_confirmed", "sanity_blocking",
                     "compliance_confirmed")}}, trace)
        self._invoke(initial, self._config(run_id))
        return self._after_invoke(run_id, trace, from_stage="START")

    def resume(self, run_id: str, gate: str, decision: dict) -> RunStatus:
        """投递人工裁定，从挂起点恢复。

        并发安全（修复轮 2）：把「读状态 → 校验 → invoke」收敛到**同一快照**——
        读出当前挂起快照的 ``checkpoint_id`` 并**钉进 invoke 的 config**，
        使续跑只能从**已校验过的那个快照**发生。若该快照已被其它进程推进
        （过期），langgraph 不会再把它应用到下一个卡点；本方法随后读到的新状态
        若与请求的 gate 不符，会抛 `InvalidGateDecision`（CLI exit 2）。
        """
        self._validate_decision(gate, decision)
        # 同一快照内取 checkpoint_id 与状态：快照先取（含 cid），状态再校验（保留可注入点）。
        snap = self._snapshot(run_id)
        current = self._read_status(run_id)
        if not current.paused:
            raise InvalidGateDecision(
                gate, f"线程 {run_id!r} 当前未挂起（stage={current.stage or '未知'}），无可恢复卡点")
        if gate != current.gate:
            raise InvalidGateDecision(
                gate, f"投错卡点：当前挂起的是 {current.gate!r}，收到 {gate!r}")
        checkpoint_id = (snap.config.get("configurable") or {}).get("checkpoint_id")
        if not checkpoint_id:
            raise InvalidGateDecision(
                gate, f"线程 {run_id!r} 找不到可恢复的检查点快照（可能已被推进）")

        trace = uuid.uuid4().hex
        self._emit("hitl.gate_resumed",
                   {"run_id": run_id, "gate": gate, "decision": decision}, trace)
        pinned = {"configurable": {"thread_id": run_id, "checkpoint_id": checkpoint_id}}
        tip_before = (self._snapshot(run_id).config.get("configurable") or {}).get("checkpoint_id")
        self._invoke(Command(resume=decision), pinned)
        tip_after = (self._snapshot(run_id).config.get("configurable") or {}).get("checkpoint_id")
        if tip_after == tip_before:
            # 钉住的是已过期快照：本次 Command 未被消费（其它进程已抢先推进），明确拒绝。
            raise InvalidGateDecision(
                gate, f"挂起快照已过期（被其它进程抢先恢复），本次裁定未生效；"
                      f"请重新读取状态（run status）后再投递")
        return self._after_invoke(run_id, trace, from_stage=current.stage)

    def status(self, run_id: str) -> RunStatus:
        """读取当前快照（不推进图）；线程不存在 → 抛 `UnknownRunError`。"""
        snap = self._snapshot(run_id)
        if getattr(snap, "created_at", None) is None and not (snap.values or {}):
            raise UnknownRunError(run_id)
        return self._status_from(run_id, snap)

    # ---- 升级路径（同阶段连续失败 → 转人工）----

    def record_failure(self, state: CoordinatorState, error: str) -> CoordinatorState:
        """记录当前阶段失败；连续失败达阈值 → 置 HALTED 并抛 HumanGateRequired。"""
        key = state.stage.value
        state.fail_counts[key] = state.fail_counts.get(key, 0) + 1
        state.context["last_error"] = error
        self._emit("coordinator.stage_failed",
                   {"stage": key, "count": state.fail_counts[key], "error": error},
                   state.trace_id)
        if state.fail_counts[key] >= MAX_CONSECUTIVE_FAILS:
            state.stage = Stage.HALTED
            raise HumanGateRequired(
                "consecutive_failures",
                f"阶段 {key} 连续失败 {state.fail_counts[key]} 次：{error}")
        return state

    def record_success(self, state: CoordinatorState) -> None:
        state.fail_counts[state.stage.value] = 0
        state.context.pop("last_error", None)

    def close(self) -> None:
        """关闭检查点库连接（测试/长驻进程可显式调用）。"""
        try:
            self._conn.close()
        except Exception:
            pass
