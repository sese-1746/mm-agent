"""probe_04b_resume.py — 跨进程恢复实验 · 阶段B：新进程从检查点续跑

完全独立的进程：重新 sqlite3.connect 同一个文件、重新 compile 图，
用 graph.get_state 读回挂起位置，再用 Command(resume=...) 逐卡点恢复。
证明「进程退出后仍可恢复」（NFR-5）。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402
import probe_graph_def as G  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe04_ckpt.sqlite")
print(f"[phase B] 新进程 pid={os.getpid()}  db={DB}  文件存在={os.path.exists(DB)}")

conn = sqlite3.connect(DB, check_same_thread=False)
saver = SqliteSaver(conn)
graph = G.build(saver)

config = {"configurable": {"thread_id": "run-42"}}

snap = graph.get_state(config)
print("[phase B] 读回检查点 snapshot.next =", snap.next)
print("[phase B] 读回 snapshot.values.stage =", snap.values.get("stage"))
for t in snap.tasks:
    print("[phase B] 待处理 task.interrupts =", t.interrupts)

# ---- 卡点1 恢复 ----
print("\n[phase B] 卡点1 恢复：Command(resume='approve')")
r = graph.invoke(Command(resume="approve"), config=config)
print("[phase B] 卡点1 后 stage =", r.get("stage"))
print("[phase B] 卡点1 后 __interrupt__ =", r.get("__interrupt__"))

# ---- 卡点2 恢复（用 dict 形式，验证 resume 值可为任意对象）----
print("\n[phase B] 卡点2 恢复：Command(resume={'approve': True, 'note': '已复核'})")
r = graph.invoke(Command(resume={"approve": True, "note": "已复核"}), config=config)
print("[phase B] 卡点2 后 stage =", r.get("stage"))
print("[phase B] 卡点2 后 __interrupt__ =", r.get("__interrupt__"))

# ---- 卡点3 恢复 ----
print("\n[phase B] 卡点3 恢复：Command(resume='approve')")
r = graph.invoke(Command(resume="approve"), config=config)
print("[phase B] 卡点3 后 stage =", r.get("stage"))
print("[phase B] 最终 log =", r.get("log"))
print("[phase B] 最终 __interrupt__ =", r.get("__interrupt__"))

fs = graph.get_state(config)
print("[phase B] 终态 snapshot.next =", fs.next, " (空=已到 END)")

# ---- 检查点历史（时间旅行）----
print("\n[phase B] 检查点历史（checkpoint_id 由新到旧）：")
n = 0
for st in graph.get_state_history(config):
    n += 1
    print(f"   #{n} next={st.next} stage={st.values.get('stage')} "
          f"cid={st.config['configurable'].get('checkpoint_id')[:8]}...")
print(f"[phase B] 历史共 {n} 条")
conn.close()
