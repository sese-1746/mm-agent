"""probe_graph_def.py — probe_04 共用的图定义（跨进程恢复实验）

三个卡点的最小化模型，直接对应 coordinator.py 的三处 TODO(LangGraph)：
  卡点1 caliber_conflict   : INTAKE  -> 人工确认 field_mapping
  卡点2 sanity_blocking    : SOLVE   -> 人工裁决阻断项
  卡点3 pre_export_compliance: ASSEMBLE -> 人工确认合规披露
每个节点内部调用 interrupt()，恢复值即人工决策（"approve"/"reject"/dict）。
"""
from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.types import interrupt


class GState(TypedDict):
    messages: Annotated[list, add_messages]
    stage: str
    caliber_conflicts: bool
    conflicts_confirmed: bool
    sanity_blocking: bool
    sanity_decision: str
    compliance_confirmed: bool
    log: Annotated[list, add_messages]


def _mark(state, **kw):
    return kw


def node_intake(state: GState):
    """卡点1：口径冲突未确认 -> interrupt"""
    if state.get("caliber_conflicts") and not state.get("conflicts_confirmed"):
        decision = interrupt({
            "gate": "caliber_conflict",
            "stage": "INTAKE",
            "question": "存在口径冲突，是否采用 field_mapping 建议？",
        })
        print(f"    [卡点1 resume 值] {decision!r}")
        ok = decision in ("approve", True) or (isinstance(decision, dict) and decision.get("approve"))
        return {"stage": "PROFILE", "conflicts_confirmed": bool(ok),
                "log": [f"gate1={decision!r}"]}
    return {"stage": "PROFILE", "log": ["gate1=skip"]}


def node_profile(state: GState):
    return {"stage": "PLAN", "log": ["profile"]}


def node_plan(state: GState):
    return {"stage": "SOLVE", "log": ["plan"]}


def node_solve(state: GState):
    """卡点2：sanity blocking -> interrupt"""
    if state.get("sanity_blocking") and not state.get("sanity_decision"):
        decision = interrupt({
            "gate": "sanity_blocking",
            "stage": "SOLVE",
            "question": "理智性检查有阻断项，如何裁决？",
        })
        print(f"    [卡点2 resume 值] {decision!r}")
        return {"stage": "RENDER", "sanity_decision": str(decision), "log": [f"gate2={decision!r}"]}
    return {"stage": "RENDER", "log": ["gate2=skip"]}


def node_render(state: GState):
    return {"stage": "ASSEMBLE", "log": ["render"]}


def node_assemble(state: GState):
    """卡点3：导出前合规确认 -> interrupt"""
    if not state.get("compliance_confirmed"):
        decision = interrupt({
            "gate": "pre_export_compliance",
            "stage": "ASSEMBLE",
            "question": "确认 AI 使用披露与合规声明后导出？",
        })
        print(f"    [卡点3 resume 值] {decision!r}")
        ok = decision in ("approve", True) or (isinstance(decision, dict) and decision.get("approve"))
        return {"stage": "EXPORT", "compliance_confirmed": bool(ok), "log": [f"gate3={decision!r}"]}
    return {"stage": "EXPORT", "log": ["gate3=skip"]}


def node_export(state: GState):
    return {"stage": "DONE", "log": ["export"]}


def build(checkpointer):
    b = StateGraph(GState)
    b.add_node("intake", node_intake)
    b.add_node("profile", node_profile)
    b.add_node("plan", node_plan)
    b.add_node("solve", node_solve)
    b.add_node("render", node_render)
    b.add_node("assemble", node_assemble)
    b.add_node("export", node_export)
    b.add_edge(START, "intake")
    b.add_edge("intake", "profile")
    b.add_edge("profile", "plan")
    b.add_edge("plan", "solve")
    b.add_edge("solve", "render")
    b.add_edge("render", "assemble")
    b.add_edge("assemble", "export")
    b.add_edge("export", END)
    return b.compile(checkpointer=checkpointer)


INITIAL = {
    "messages": [("human", "跑一次三卡点流程")],
    "stage": "INTAKE",
    "caliber_conflicts": True,
    "conflicts_confirmed": False,
    "sanity_blocking": True,
    "sanity_decision": "",
    "compliance_confirmed": False,
    "log": [],
}
