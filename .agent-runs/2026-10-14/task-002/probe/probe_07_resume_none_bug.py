"""probe_07_resume_none_bug.py — 隔离 Command(resume=None) 的 UnboundLocalError"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from typing import Annotated  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402
from langgraph.graph import StateGraph, START, END  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import interrupt, Command  # noqa: E402
import langgraph.types as _lt  # noqa: E402


class S(TypedDict):
    log: Annotated[list, add_messages]


def node(state: S):
    v = interrupt("ask")
    return {"log": [f"got={v!r}"]}


b = StateGraph(S)
b.add_node("n", node)
b.add_edge(START, "n")
b.add_edge("n", END)
g = b.compile(checkpointer=MemorySaver())
cfg = {"configurable": {"thread_id": "t_none"}}
g.invoke({"log": []}, config=cfg)
print("已挂起，现用 Command(resume=None) 恢复：")
try:
    g.invoke(Command(resume=None), config=cfg)
    print("未抛异常（与 probe_06 结果不一致）")
except Exception:  # noqa: BLE001
    traceback.print_exc()
    print()
    print("langgraph.types.__file__ =", _lt.__file__)
