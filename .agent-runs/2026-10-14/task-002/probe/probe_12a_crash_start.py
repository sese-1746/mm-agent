"""probe_12a (Max) — 跨进程恢复 · 阶段A：用真实 Coordinator 跑到卡点后 os._exit(0) 硬崩溃。

模拟 NFR-5「进程非正常退出」：不 close 连接、不做任何清理。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
from mmagent.agents.coordinator import Coordinator  # noqa: E402

CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe12_ckpt.db")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe12_events.db")
for f in (CKPT, CKPT + "-wal", CKPT + "-shm", DB):
    if os.path.exists(f):
        os.remove(f)

from mmagent.store.db import init_db  # noqa: E402
init_db(DB)

print(f"[A] pid={os.getpid()}")
c = Coordinator(db_path=DB, ckpt_path=CKPT)
st = c.start("crash-run", {"caliber_conflicts": True, "sanity_blocking": True})
print("[A] start ->", st.to_dict())
print("[A] 模拟崩溃：os._exit(0)（不 close、不清理）")
sys.stdout.flush()
os._exit(0)
