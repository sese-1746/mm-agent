"""probe_12b (Max) — 跨进程恢复 · 阶段B：新进程仅凭 SQLite 检查点续跑到 DONE。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
from mmagent.agents.coordinator import Coordinator  # noqa: E402

CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe12_ckpt.db")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe12_events.db")

print(f"[B] 新进程 pid={os.getpid()}  检查点文件存在={os.path.exists(CKPT)}")
c = Coordinator(db_path=DB, ckpt_path=CKPT)
print("[B] 读回 status ->", c.status("crash-run").to_dict())

st = c.resume("crash-run", "caliber_conflict", {"resolution": "keep_raw", "note": "保留原始口径"})
print("[B] resume1 ->", st.to_dict())
st = c.resume("crash-run", "sanity_blocking", {"decision": "accept", "note": "复核通过"})
print("[B] resume2 ->", st.to_dict())
st = c.resume("crash-run", "pre_export_compliance", {"decision": "approve", "note": "合规确认"})
print("[B] resume3 ->", st.to_dict())

fin = c.status("crash-run")
print("[B] 终态 done =", fin.done, " stage =", fin.stage)
c.close()
print("[B] 跨进程恢复完成")
