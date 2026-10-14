"""probe_13 (Max) — 修复轮 2 前置实验：
 (a) get_state 未知 thread 的可辨识特征（F4）
 (b) invoke 时用 config 钉住（陈旧的）checkpoint_id 会发生什么（F3 关键）
"""
import os
import sqlite3
import sys
import tempfile
from operator import add

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
from typing import Annotated  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402
from langgraph.graph import StateGraph, START, END  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import interrupt, Command  # noqa: E402


class S(TypedDict):
    stage: str
    decisions: dict
    log: Annotated[list, add]


def g1(state):
    if "g1" not in state.get("decisions", {}):
        v = interrupt({"gate": "g1"})
        return {"decisions": {**state.get("decisions", {}), "g1": v}, "log": ["g1"]}
    return {"log": ["g1skip"]}


def g2(state):
    if "g2" not in state.get("decisions", {}):
        v = interrupt({"gate": "g2"})
        return {"decisions": {**state.get("decisions", {}), "g2": v}, "log": ["g2"]}
    return {"log": ["g2skip"]}


def export(state):
    return {"stage": "DONE", "log": ["export"]}


def build(cp):
    b = StateGraph(S)
    b.add_node("g1", g1)
    b.add_node("mid", lambda s: {"stage": "MID", "log": ["mid"]})
    b.add_node("g2", g2)
    b.add_node("export", export)
    b.add_edge(START, "g1")
    b.add_edge("g1", "mid")
    b.add_edge("mid", "g2")
    b.add_edge("g2", "export")
    b.add_edge("export", END)
    return b.compile(checkpointer=cp)


tmp = tempfile.mkdtemp()
conn = sqlite3.connect(os.path.join(tmp, "c.db"), check_same_thread=False, timeout=5.0)
g = build(SqliteSaver(conn))
cfg = {"configurable": {"thread_id": "t"}}

print("== (a) 未知 thread 特征 ==")
st = g.get_state({"configurable": {"thread_id": "nope"}})
print("values:", dict(st.values), "| next:", st.next, "| tasks:", st.tasks,
      "| created_at:", getattr(st, "created_at", "<no attr>"),
      "| config:", st.config)

print("== 跑到 g1 挂起 ==")
g.invoke({"stage": "INIT", "decisions": {}, "log": []}, config=cfg)
snap1 = g.get_state(cfg)
cid1 = snap1.config["configurable"]["checkpoint_id"]
print("paused next:", snap1.next, "cid1:", cid1[:8], "values.log:", snap1.values["log"])

print("== 正常 resume g1 ==")
g.invoke(Command(resume={"resolution": "A"}), config=cfg)
snap2 = g.get_state(cfg)
cid2 = snap2.config["configurable"]["checkpoint_id"]
print("now next:", snap2.next, "cid2:", cid2[:8], "values.log:", snap2.values["log"])

print("== 关键：用陈旧 cid1 钉住 + Command(resume=口径形状) ==")
try:
    r = g.invoke(Command(resume={"resolution": "STALE"}),
                 config={"configurable": {"thread_id": "t", "checkpoint_id": cid1}})
    print("陈旧钉住 invoke 返回 keys:", list(r.keys()), "| __interrupt__:",
          bool(r.get("__interrupt__")))
    eff = g.get_state(cfg)
    print("之后 tip next:", eff.next, "| decisions:", eff.values.get("decisions"),
          "| log:", eff.values.get("log"))
except Exception as exc:  # noqa: BLE001
    print("陈旧钉住 invoke 抛:", type(exc).__name__, exc)

print("== 参考：不存在 cid ==")
try:
    g.invoke(Command(resume={"resolution": "X"}),
             config={"configurable": {"thread_id": "t", "checkpoint_id": "00000000-0000-0000-0000-000000000000"}})
    print("不存在 cid：未抛")
except Exception as exc:  # noqa: BLE001
    print("不存在 cid 抛:", type(exc).__name__, exc)
conn.close()
print("probe_13 完成")
