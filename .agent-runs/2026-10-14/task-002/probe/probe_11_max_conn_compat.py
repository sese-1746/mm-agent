"""probe_11 (Max) — 开工前兼容性小实验（不属调查员探针）。

结论导向：
 (1) store.db.connect() 不兼容 —— 它没设 check_same_thread=False，
     而 langgraph 在 worker 线程里 put 检查点 → ProgrammingError。
 (2) 裸 sqlite3.connect(..., check_same_thread=False, timeout=5.0) + PRAGMA busy_timeout 可用。
 (3) get_state 未知 thread_id 的返回值（status() 要处理）。
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
    log: Annotated[list, add]


def node(state):
    v = interrupt({"gate": "g", "stage": "S", "question": "q"})
    return {"stage": "DONE", "log": [f"got={v!r}"]}


def build(cp):
    b = StateGraph(S)
    b.add_node("n", node)
    b.add_edge(START, "n")
    b.add_edge("n", END)
    return b.compile(checkpointer=cp)


tmp = tempfile.mkdtemp()
p2 = os.path.join(tmp, "ck_bare.db")
conn2 = sqlite3.connect(p2, check_same_thread=False, timeout=5.0)
conn2.execute("PRAGMA busy_timeout=5000;")
conn2.execute("PRAGMA journal_mode=WAL;")
g = build(SqliteSaver(conn2))
cfg = {"configurable": {"thread_id": "t2"}}
r = g.invoke({"stage": "S", "log": []}, config=cfg)
print("(2) 裸连接挂起 __interrupt__ =", r.get("__interrupt__"))
snap = g.get_state(cfg)
print("(2) paused snapshot.next =", snap.next, "tasks[0].interrupts =",
      snap.tasks[0].interrupts if snap.tasks else None)

# (3) 未知 thread
st = g.get_state({"configurable": {"thread_id": "nope"}})
print("(3) 未知 thread: values =", dict(st.values), "next =", st.next, "tasks =", st.tasks)

r2 = g.invoke(Command(resume={"resolution": "use_mapping", "note": "x"}), config=cfg)
print("(2) 恢复后 stage =", r2.get("stage"), "log =", r2.get("log"),
      "__interrupt__ =", r2.get("__interrupt__"))
fin = g.get_state(cfg)
print("(2) 终态 next =", fin.next, " (空=END)")
conn2.close()
print("probe_11 完成 — 裸连接路径 OK")
