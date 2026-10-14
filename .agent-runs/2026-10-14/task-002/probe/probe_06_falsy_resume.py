"""probe_06_falsy_resume.py — 边界：resume 值为「假值」/ None 时能否恢复

实现方关心：人工决策可能是 False / 0 / "" / None，这些假值能否正常投递？
以及 invoke(None) 与 Command(resume=None) 是否等价（都等于「不给值」）。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from typing import Annotated  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402
from langgraph.graph import StateGraph, START, END  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import interrupt, Command  # noqa: E402


class S(TypedDict):
    log: Annotated[list, add_messages]


GOT = []


def node(state: S):
    v = interrupt("ask")
    GOT.append(v)
    return {"log": [f"got={v!r}"]}


def build():
    b = StateGraph(S)
    b.add_node("n", node)
    b.add_edge(START, "n")
    b.add_edge("n", END)
    return b.compile(checkpointer=MemorySaver())


def trial(label, resume_factory):
    global GOT
    GOT = []
    g = build()
    cfg = {"configurable": {"thread_id": f"t_{label}"}}
    g.invoke({"log": []}, config=cfg)
    try:
        r = g.invoke(resume_factory(), config=cfg)
        interrupted = "__interrupt__" in r and bool(r["__interrupt__"])
        snap = g.get_state(cfg)
        print(f"  [{label:34s}] 收到值={GOT!r:22s} 仍挂起={interrupted} next={snap.next}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label:34s}] 异常 {type(exc).__name__}: {exc}")


print("=" * 78)
print("resume 值真假性测试（观察 interrupt() 实际收到的值 & 是否仍挂起）")
print("=" * 78)
trial("Command(resume=None)", lambda: Command(resume=None))
trial("Command(resume=False)", lambda: Command(resume=False))
trial("Command(resume=0)", lambda: Command(resume=0))
trial("Command(resume='')", lambda: Command(resume=""))
trial("Command(resume=[])", lambda: Command(resume=[]))
trial("Command(resume={})", lambda: Command(resume={}))
trial("Command(resume='approve')", lambda: Command(resume="approve"))
trial("Command(resume={'approve': False})", lambda: Command(resume={"approve": False}))
trial("invoke(None)  [无 Command]", lambda: None)
print()
print("判定：见输出 —— 假值是否被投递、invoke(None) 是否等同于不给 resume 值。")
print("[probe_06] 完成")
