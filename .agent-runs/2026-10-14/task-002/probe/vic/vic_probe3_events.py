"""Vic 验证项3：每个卡点 hitl.gate_paused 恰好 1 条（同一 (run_id, gate)）。

含：单进程多次 resume/status；跨进程续跑；额外失败 resume 与只读 status 都不膨胀。
用原生 sqlite3 直读台账库 events 表统计。
"""
import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
PY = sys.executable
sys.path.insert(0, REPO)

from mmagent.agents.coordinator import Coordinator, InvalidGateDecision  # noqa: E402
from mmagent.store.db import init_db  # noqa: E402

LEGAL = {
    "caliber_conflict": {"resolution": "use_mapping", "note": "n"},
    "sanity_blocking": {"decision": "accept", "note": "n"},
    "pre_export_compliance": {"decision": "approve", "note": "n"},
}

TMP = os.path.join(HERE, "ws3")
os.makedirs(TMP, exist_ok=True)
fails = []


def check(cond, label):
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        fails.append(label)


def counts(db, run_id, event_type):
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT json_extract(payload,'$.gate') AS gate, COUNT(*) AS n FROM events "
            "WHERE event_type=? AND json_extract(payload,'$.run_id')=? GROUP BY gate",
            (event_type, run_id)).fetchall()
        return {g: n for g, n in rows}
    finally:
        conn.close()


def total(db, event_type):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?",
                            (event_type,)).fetchone()[0]
    finally:
        conn.close()


print("=" * 78)
print("【验证项3a】单进程：3 卡点走完 + 额外 status + 失败 resume")
print("=" * 78)
db = os.path.join(TMP, "mm.db")
ckpt = os.path.join(TMP, "ckpt.db")
for f in (db, ckpt, ckpt + "-wal", ckpt + "-shm"):
    if os.path.exists(f):
        os.remove(f)
init_db(db)
RUN = "evt-single"
c = Coordinator(db_path=db, ckpt_path=ckpt)
try:
    st = c.start(RUN, {"caliber_conflicts": True, "sanity_blocking": True})
    n_resume = 0
    while st.paused:
        st = c.resume(RUN, st.gate, LEGAL[st.gate])
        n_resume += 1
    check(st.done, "运行到 DONE")
    # 额外读 / 失败 resume（跑到 DONE 后再投裁定）
    for _ in range(3):
        c.status(RUN)
    try:
        c.resume(RUN, "pre_export_compliance", LEGAL["pre_export_compliance"])
        check(False, "DONE 后投裁定应被拦")
    except InvalidGateDecision:
        check(True, "DONE 后投裁定被 InvalidGateDecision 拦下")
finally:
    c.close()

paused_counts = counts(db, RUN, "hitl.gate_paused")
print("  hitl.gate_paused per gate:", paused_counts)
check(paused_counts == {"caliber_conflict": 1, "sanity_blocking": 1,
                        "pre_export_compliance": 1},
      "每卡点 hitl.gate_paused == 1")
check(total(db, "hitl.gate_paused") == 3, "gate_paused 总数 == 3（额外读/失败不膨胀）")
check(total(db, "hitl.gate_resumed") == 3, "gate_resumed 总数 == 3")
check(total(db, "hitl.run_started") == 1, "run_started 总数 == 1")
check(total(db, "hitl.run_finished") == 1, "run_finished 总数 == 1")

print()
print("=" * 78)
print("【验证项3b】跨进程：每步骤独立 CLI 子进程续跑")
print("=" * 78)
xdb = os.path.join(TMP, "xp_mm.db")
xckpt = os.path.join(TMP, "xp_ckpt.db")
for f in (xdb, xckpt, xckpt + "-wal", xckpt + "-shm"):
    if os.path.exists(f):
        os.remove(f)
XRUN = "evt-xp"


def cli(*args):
    return subprocess.run([PY, "-m", "mmagent.cli", *args], cwd=REPO,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


cli("db", "init", "--db", xdb)
r = cli("run", "start", "--run-id", XRUN, "--conflicts", "--sanity-blocking",
        "--ckpt", xckpt, "--db", xdb)
check(r.returncode == 0, "跨进程 start OK")
for gate in ("caliber_conflict", "sanity_blocking", "pre_export_compliance"):
    r = cli("run", "resume", "--run-id", XRUN, "--gate", gate,
            "--decision", json.dumps(LEGAL[gate]), "--ckpt", xckpt, "--db", xdb)
    check(r.returncode == 0, f"跨进程 resume {gate} OK")

xp = counts(xdb, XRUN, "hitl.gate_paused")
print("  跨进程 hitl.gate_paused per gate:", xp)
check(xp == {"caliber_conflict": 1, "sanity_blocking": 1, "pre_export_compliance": 1},
      "跨进程每卡点 hitl.gate_paused == 1")
check(total(xdb, "hitl.gate_resumed") == 3, "跨进程 gate_resumed == 3")

print()
if fails:
    print("【验证项3】RESULT = FAIL, 失败项:", fails)
    sys.exit(1)
print("【验证项3】RESULT = PASS")
sys.exit(0)
