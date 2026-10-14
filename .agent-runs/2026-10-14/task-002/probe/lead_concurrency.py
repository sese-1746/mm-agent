"""队长对抗性验证：并发恢复不得串台（task-002 Phase 4）。

两进程同时恢复同一挂起点（caliber_conflict），多轮统计：
- 合规卡点（pre_export_compliance）**不得**被口径形状的裁定跨过
  （症状：log 出现 'gate:pre_export_compliance=None' 或非 approve/reject 值）；
- 任何进程都不得裸 traceback（rc=1）；允许受控 exit(2)（陈旧快照被拒）。

只读被测实现；产物落本探针目录。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PY = sys.executable
WS = HERE / "lead_ws_conc"
if WS.exists():
    shutil.rmtree(WS)
WS.mkdir(parents=True)

ROUNDS = 4
ENV = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONIOENCODING": "utf-8"}

RESUME_BODY = '''
import sys
sys.path.insert(0, {repo!r})
from mmagent.agents.coordinator import Coordinator, InvalidGateDecision
c = Coordinator(db_path={db!r}, ckpt_path={ckpt!r})
try:
    st = c.resume({run!r}, "caliber_conflict", {{"resolution": "use_mapping", "note": "n"}})
    print("RC0", st.stage, st.paused, st.gate, st.done)
except InvalidGateDecision as e:
    print("RC2", str(e))
except Exception as e:
    print("RC1", type(e).__name__, str(e)[:120])
'''

START_BODY = '''
import sys
sys.path.insert(0, {repo!r})
from mmagent.agents.coordinator import Coordinator
c = Coordinator(db_path={db!r}, ckpt_path={ckpt!r})
st = c.start({run!r}, {{"caliber_conflicts": True}})
print(st.stage, st.paused, st.gate)
c.close()
'''

STATUS_BODY = '''
import sys, json
sys.path.insert(0, {repo!r})
from mmagent.agents.coordinator import Coordinator
c = Coordinator(db_path={db!r}, ckpt_path={ckpt!r})
st = c.status({run!r})
print("STATUS", json.dumps({{"stage": st.stage, "log": st.log}}, ensure_ascii=False))
c.close()
'''


def spawn(name: str, body: str):
    p = WS / name
    p.write_text(body, encoding="utf-8")
    return subprocess.Popen([PY, str(p)], cwd=str(REPO), env=ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")


xtalk = 0
raw_tb = 0
for i in range(ROUNDS):
    run = f"race-{i}"
    db = WS / f"r{i}.db"
    ckpt = WS / f"r{i}.ckpt.db"
    subprocess.run([PY, "-m", "mmagent.cli", "db", "init", "--db", str(db)],
                   cwd=str(REPO), env=ENV, capture_output=True, timeout=60)
    spawn(f"s{i}.py", START_BODY.format(repo=str(REPO), db=str(db), ckpt=str(ckpt), run=run)).communicate(timeout=120)
    pa = spawn(f"a{i}.py", RESUME_BODY.format(repo=str(REPO), db=str(db), ckpt=str(ckpt), run=run))
    pb = spawn(f"b{i}.py", RESUME_BODY.format(repo=str(REPO), db=str(db), ckpt=str(ckpt), run=run))
    oa, ea = pa.communicate(timeout=180)
    ob, eb = pb.communicate(timeout=180)
    ra, rb = pa.returncode, pb.returncode
    if ra == 1 or rb == 1:
        raw_tb += 1
    ps = subprocess.run([PY, "-c", STATUS_BODY.format(repo=str(REPO), db=str(db), ckpt=str(ckpt), run=run)],
                        cwd=str(REPO), env=ENV, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=120)
    log = []
    for ln in ps.stdout.splitlines():
        if ln.startswith("STATUS "):
            log = json.loads(ln[7:])["log"]
    bad = any(entry.startswith("gate:pre_export_compliance=")
              and entry.split("=", 1)[1] not in ("approve", "reject") for entry in log)
    if bad:
        xtalk += 1
    print(f"round {i}: rcs=({ra},{rb}) a={oa.strip()[:44]!r} b={ob.strip()[:44]!r} "
          f"crossed_compliance={bad} log_tail={log[-2:] if log else []}", flush=True)

print(f"\n==> {ROUNDS} 轮：跨台命中={xtalk}（期望 0）；裸 traceback(rc=1)={raw_tb}（期望 0）")
sys.exit(1 if (xtalk or raw_tb) else 0)
