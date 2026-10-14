"""队长 Horizon 独立复算探针 —— task-002 Phase 4（不采信纸面结论）。

对抗性要点：
- 真实「挂起 → os._exit(0) 硬杀 → 新进程重启 → 恢复」跨进程演示；
- 用**原生 sqlite3**直读检查点库做旁证（绕过应用层）；
- 非法裁定（None / 空 dict / 投错 gate）在应用层被拦，且**不得**出现 langgraph UnboundLocalError；
- 对照：绕过 _validate_decision 直连 langgraph 用 Command(resume=None) 复现 UnboundLocalError；
- 同一 (run_id, gate) 的 hitl.gate_paused 恰好 1 条。

只读被测实现；产物全部落在本探针目录（不碰 data/、artifacts/）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]              # probe -> task-002 -> 2026-10-14 -> .agent-runs -> H:\mm-agent
assert (REPO / "mmagent" / "agents" / "coordinator.py").exists(), f"REPO 解析错误：{REPO}"
PY = sys.executable
WS = HERE / "lead_ws"
if WS.exists():
    shutil.rmtree(WS)
WS.mkdir(parents=True)

DB = WS / "mm.db"
CK = WS / "ckpt.db"

FAIL = []


def check(cond: bool, msg: str):
    tag = "OK  " if cond else "FAIL"
    if not cond:
        FAIL.append(msg)
    print(f"[{tag}] {msg}", flush=True)


def run(args, **kw):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([PY, *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, **kw)


def driver(name: str, body: str) -> Path:
    p = WS / name
    p.write_text(
        "import sys, os, json\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        f"DB = {str(DB)!r}\nCK = {str(CK)!r}\n"
        "from mmagent.agents.coordinator import Coordinator\n"
        + body, encoding="utf-8")
    return p


print("=" * 70)
print("STEP 0  init_db（显式初始化台账库）")
r = run(["-m", "mmagent.cli", "db", "init", "--db", str(DB)])
check(r.returncode == 0, f"db init rc={r.returncode}")
check(DB.exists(), "台账库已创建")

print("=" * 70)
print("STEP 1  真实挂起 + os._exit(0) 硬杀（不 close、不清理 WAL）")
d1 = driver("s1_start_crash.py", (
    "c = Coordinator(db_path=DB, ckpt_path=CK)\n"
    "st = c.start('crash-1', {'caliber_conflicts': True, 'sanity_blocking': True})\n"
    "print('START', st.stage, st.paused, st.gate)\n"
    "sys.stdout.flush()\n"
    "os._exit(0)\n"))
r1 = run([str(d1)])
print("child rc=", r1.returncode, "stdout=", r1.stdout.strip())
check(r1.returncode == 0, "硬杀子进程 rc==0（os._exit(0)）")
check(CK.exists(), "检查点库已落盘")

print("=" * 70)
print("STEP 2  原生 sqlite3 直读检查点库（绕过应用层对照）")
conn = sqlite3.connect(str(CK))
tables = [t[0] for t in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("ckpt tables =", tables)
n_ck = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
n_threads = conn.execute(
    "SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]
print(f"checkpoints rows={n_ck} distinct thread_id={n_threads}")
check(n_ck >= 1, "checkpoints 表有数据（挂起态已持久化）")
threads = [t[0] for t in conn.execute(
    "SELECT DISTINCT thread_id FROM checkpoints").fetchall()]
check("crash-1" in str(threads), f"thread_id 含 crash-1（实际 {threads}）")
conn.close()

print("=" * 70)
print("STEP 3  新进程仅凭同一 ckpt 续跑到 DONE")
d2 = driver("s3_resume_all.py", (
    "c = Coordinator(db_path=DB, ckpt_path=CK)\n"
    "s0 = c.status('crash-1')\n"
    "print('STATUS', s0.stage, s0.paused, s0.gate)\n"
    "s1 = c.resume('crash-1', 'caliber_conflict', {'resolution': 'use_mapping', 'note': 'n'})\n"
    "print('R1', s1.stage, s1.paused, s1.gate)\n"
    "s2 = c.resume('crash-1', 'sanity_blocking', {'decision': 'accept', 'note': 'n'})\n"
    "print('R2', s2.stage, s2.paused, s2.gate)\n"
    "s3 = c.resume('crash-1', 'pre_export_compliance', {'decision': 'approve', 'note': 'n'})\n"
    "print('R3', s3.stage, s3.paused, s3.done)\n"
    "print('LOG', json.dumps(s3.log, ensure_ascii=False))\n"
    "print('DONE', s3.done, s3.stage)\n"
    "c.close()\n"))
r3 = run([str(d2)])
print(r3.stdout.strip())
if r3.returncode != 0:
    print("  子进程 stderr:", r3.stderr.strip()[-800:])
check(r3.returncode == 0, f"续跑子进程 rc==0（实际 {r3.returncode}）")
lines = dict()
for ln in r3.stdout.splitlines():
    if ln.startswith("STATUS "):
        _, st, paused, gate = ln.split(maxsplit=3)
        check(paused == "True" and gate == "caliber_conflict",
              f"新进程读回挂起态 = caliber_conflict（实际 {gate}, paused={paused}）")
    if ln.startswith("DONE "):
        _, done, stage = ln.split()
        check(done == "True" and stage == "DONE", f"终态 DONE/done=True（实际 {stage}/{done}）")
    if ln.startswith("LOG "):
        log = json.loads(ln[4:])
        check("stage:export" in log, "log 含 stage:export（真正跑完 export）")
        check(log.count("stage:intake") == 1, "无重复 intake（节点未整体重跑）")

print("=" * 70)
print("STEP 4  非法裁定：应用层拦 + 不触发 UnboundLocalError")
d3 = driver("s4_invalid.py", (
    "from mmagent.agents.coordinator import InvalidGateDecision\n"
    "c = Coordinator(db_path=DB, ckpt_path=CK)\n"
    "c.start('inv-1', {'caliber_conflicts': True})\n"
    "import traceback\n"
    "for label, gate, dec in [('None', 'caliber_conflict', None),\n"
    "                         ('empty', 'caliber_conflict', {}),\n"
    "                         ('wronggate', 'pre_export_compliance', {'decision':'approve','note':'x'})]:\n"
    "    try:\n"
    "        c.resume('inv-1', gate, dec)\n"
    "        print('RES', label, 'NO-RAISE')\n"
    "    except InvalidGateDecision as e:\n"
    "        print('RES', label, 'InvalidGateDecision', 'isNameError=', isinstance(e, NameError))\n"
    "    except Exception as e:\n"
    "        print('RES', label, type(e).__name__, 'isNameError=', isinstance(e, NameError))\n"
    "print('STILL', c.status('inv-1').gate, c.status('inv-1').paused)\n"
    "c.close()\n"))
r4 = run([str(d3)])
print(r4.stdout.strip())
for ln in r4.stdout.splitlines():
    if ln.startswith("RES "):
        parts = ln.split()
        label = parts[1]
        check(parts[2] == "InvalidGateDecision",
              f"非法裁定[{label}] 被 InvalidGateDecision 拦截（实际 {parts[2]}）")
        check(parts[-1] == "False",
              f"非法裁定[{label}] 非 NameError/UnboundLocalError（isNameError=False）")
    if ln.startswith("STILL "):
        check("caliber_conflict" in ln, "非法裁定后状态仍停在原卡点（未被污染）")

print("=" * 70)
print("STEP 5  对照：绕过 _validate_decision 直连 langgraph，resume=None → UnboundLocalError?")
d5 = driver("s5_bypass.py", (
    "from langgraph.types import Command\n"
    "c = Coordinator(db_path=DB, ckpt_path=CK)\n"
    "c.start('byp-1', {'caliber_conflicts': True})\n"
    "try:\n"
    "    c._graph.invoke(Command(resume=None), config={'configurable': {'thread_id': 'byp-1'}})\n"
    "    print('BYPASS no-raise')\n"
    "except Exception as e:\n"
    "    print('BYPASS', type(e).__name__, isinstance(e, NameError))\n"
    "c.close()\n"))
r5 = run([str(d5)])
print(r5.stdout.strip())
for ln in r5.stdout.splitlines():
    if ln.startswith("BYPASS "):
        parts = ln.split()
        check(parts[1] == "UnboundLocalError",
              f"对照：裸 resume=None 触发 UnboundLocalError（实际 {parts[1]}）-> 守卫必要")

print("=" * 70)
print("STEP 6  hitl.gate_paused 每个 (run_id,gate) 恰好 1 条")
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT json_extract(payload,'$.run_id') rid, json_extract(payload,'$.gate') g,"
    " COUNT(*) c FROM events WHERE event_type='hitl.gate_paused' GROUP BY rid, g").fetchall()
for row in rows:
    print("  paused", dict(row))
crash_rows = [dict(r) for r in rows if r["rid"] == "crash-1"]
check(len(crash_rows) == 3 and all(r["c"] == 1 for r in crash_rows),
      f"crash-1 三卡点各 1 条 gate_paused（实际 {crash_rows}）")
types = conn.execute(
    "SELECT event_type, COUNT(*) c FROM events GROUP BY event_type").fetchall()
print("  event types:", {r["event_type"]: r["c"] for r in types})
fin = conn.execute(
    "SELECT COUNT(*) FROM events WHERE event_type='hitl.run_finished'"
    " AND json_extract(payload,'$.run_id')='crash-1'").fetchone()[0]
check(fin == 1, f"crash-1 恰 1 条 run_finished（实际 {fin}）")
conn.close()

print("=" * 70)
if FAIL:
    print(f"LEAD-CORE RESULT: FAIL ({len(FAIL)} 项)")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("LEAD-CORE RESULT: PASS（全部断言通过）")
