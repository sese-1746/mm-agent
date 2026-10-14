"""Vic 硬杀探针：在新的 python 进程中投一次裁定后 **os._exit(0)** 硬杀。

不 close、不 WAL checkpoint、不做任何清理。用于证明「挂起态/推进态完全落在磁盘」，
新进程可仅凭同一 ckpt 库继续。

用法: python vic_hardkill_resume.py <db> <ckpt> <run_id> <gate> <decision_json>
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "..", "..", ".."))
sys.path.insert(0, REPO)

from mmagent.agents.coordinator import Coordinator  # noqa: E402

db, ckpt, run_id, gate, decision_json = sys.argv[1:6]
print("__PID__=%d" % os.getpid(), flush=True)
c = Coordinator(db_path=db, ckpt_path=ckpt)
st = c.resume(run_id, gate, json.loads(decision_json))
print("__RESUME__=" + json.dumps(st.to_dict(), ensure_ascii=False), flush=True)
sys.stdout.flush()
os._exit(0)  # 硬杀：不 close、不清理 WAL
