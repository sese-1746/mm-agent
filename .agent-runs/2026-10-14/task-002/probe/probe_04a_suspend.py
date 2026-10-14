"""probe_04a_suspend.py — 跨进程恢复实验 · 阶段A：跑到卡点并「崩溃」退出

用 os._exit(0) 硬退出，模拟进程崩溃（不执行任何清理/close），
验证 NFR-5：崩溃后检查点是否已落盘、新进程能否续跑。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
import probe_graph_def as G  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe04_ckpt.sqlite")
if os.path.exists(DB):
    os.remove(DB)
print(f"[phase A] pid={os.getpid()}  db={DB}")

conn = sqlite3.connect(DB, check_same_thread=False)
saver = SqliteSaver(conn)
graph = G.build(saver)

config = {"configurable": {"thread_id": "run-42"}}
res = graph.invoke(dict(G.INITIAL), config=config)
print("[phase A] invoke 返回 keys =", list(res.keys()))
print("[phase A] __interrupt__     =", res.get("__interrupt__"))
snap = graph.get_state(config)
print("[phase A] snapshot.next     =", snap.next)
print("[phase A] snapshot.values.stage =", snap.values.get("stage"))
for t in snap.tasks:
    print("[phase A] task.interrupts   =", t.interrupts)
print("[phase A] 库内表 =", [r[0] for r in conn.execute(
    "select name from sqlite_master where type='table'").fetchall()])
print("[phase A] checkpoint 行数 =", conn.execute(
    "select count(*) from checkpoints").fetchone()[0])
print("[phase A] 模拟崩溃：os._exit(0)，不 close 连接、不做清理")
sys.stdout.flush()
os._exit(0)
