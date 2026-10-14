"""Vic 验证项7：并发恢复不串台（对抗性）+ 只读检查点库受控退出。

(1) N 轮「两个独立 CLI 进程同时恢复同一挂起点」→ 跨台命中 = 0
    （合规卡点绝不被非法裁定跨过）；收集 returncode、原始 traceback 情况。
(2) 只读检查点库 → 受控退出（明确 exit code，无原始 traceback）。
"""
import json
import os
import sqlite3
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
PY = sys.executable

TMP = os.path.join(HERE, "ws7")
os.makedirs(TMP, exist_ok=True)
LEGAL = {"resolution": "use_mapping", "note": "按建议口径合并"}
ROUNDS = 10  # 对抗性：比 Max 的 8 轮更多


def cli(*args):
    return subprocess.run([PY, "-m", "mmagent.cli", *args], cwd=REPO,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def decisions_of(ckpt, db, run_id):
    """原生 sqlite3 直读该 thread 的 decisions（经 langgraph 反序列化 state）。"""
    code = (
        "import json;"
        "from mmagent.agents.coordinator import Coordinator;"
        f"c=Coordinator(db_path=r'{db}', ckpt_path=r'{ckpt}');"
        f"s=c._graph.get_state({{'configurable':{{'thread_id':'{run_id}'}}}});"
        "print(json.dumps(dict((s.values or {}).get('decisions', {})), ensure_ascii=False));"
        "c.close()"
    )
    r = subprocess.run([PY, "-c", code], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return json.loads(r.stdout.strip().splitlines()[-1])


print("=" * 78)
print(f"【验证项7.1】{ROUNDS} 轮并发恢复同一挂起点 → 跨台命中 = 0")
print("=" * 78)
cross_hits = 0
rac = 0
raw_tb_rounds = 0
for i in range(ROUNDS):
    d = os.path.join(TMP, f"race_{i}")
    os.makedirs(d, exist_ok=True)
    db = os.path.join(d, "mm.db")
    ckpt = os.path.join(d, "ckpt.db")
    for f in (db, ckpt, ckpt + "-wal", ckpt + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    assert cli("db", "init", "--db", db).returncode == 0
    r0 = cli("run", "start", "--run-id", f"r{i}", "--conflicts", "--db", db, "--ckpt", ckpt)
    assert r0.returncode == 0, r0.stderr
    resume = ["run", "resume", "--run-id", f"r{i}", "--gate", "caliber_conflict",
              "--decision", json.dumps(LEGAL, ensure_ascii=False), "--db", db, "--ckpt", ckpt]
    p1 = subprocess.Popen([PY, "-m", "mmagent.cli", *resume], cwd=REPO,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    p2 = subprocess.Popen([PY, "-m", "mmagent.cli", *resume], cwd=REPO,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    o1, e1 = p1.communicate()
    o2, e2 = p2.communicate()
    dec = decisions_of(ckpt, db, f"r{i}")
    crossed = dec.get("pre_export_compliance")
    # 跨台命中 = 合规卡点被非合法裁定（口径形状）跨过
    bad = crossed is not None and (crossed or {}).get("decision") not in {"approve", "reject"}
    raw_tb = ("Traceback" in (e1 or "")) or ("Traceback" in (e2 or ""))
    if bad:
        cross_hits += 1
    if raw_tb:
        raw_tb_rounds += 1
    # 计数「受控拒绝」轮（rc==2）
    rcs = (p1.returncode, p2.returncode)
    if 2 in rcs:
        rac += 1
    print(f"  round {i}: rc={rcs} 合规裁定={crossed} 跨台={'是' if bad else '否'} "
          f"原始traceback={'有' if raw_tb else '无'}")
print(f"==> {ROUNDS} 轮跨台命中 = {cross_hits}（期望 0）；含受控拒绝(rc=2)的轮 = {rac}；"
      f"含原始 traceback 的轮 = {raw_tb_rounds}（期望 0）")

print()
print("=" * 78)
print("【验证项7.2】只读检查点库 → 受控退出")
print("=" * 78)
d2 = os.path.join(TMP, "ro")
os.makedirs(d2, exist_ok=True)
db2 = os.path.join(d2, "mm.db")
ckpt2 = os.path.join(d2, "ckpt.db")
for f in (db2, ckpt2, ckpt2 + "-wal", ckpt2 + "-shm"):
    if os.path.exists(f):
        os.remove(f)
cli("db", "init", "--db", db2)
cli("run", "start", "--run-id", "ro-1", "--conflicts", "--db", db2, "--ckpt", ckpt2)
touched = [ckpt2, ckpt2 + "-wal", ckpt2 + "-shm"]
for p in touched:
    if os.path.exists(p):
        os.chmod(p, stat.S_IREAD)
try:
    r = cli("run", "resume", "--run-id", "ro-1", "--gate", "caliber_conflict",
            "--decision", json.dumps(LEGAL, ensure_ascii=False), "--db", db2, "--ckpt", ckpt2)
    print("  returncode =", r.returncode)
    print("  stderr =", (r.stderr or "").strip()[:300])
    print("  含原始 traceback?", "Traceback" in (r.stderr or ""))
    ro_ok = r.returncode == 3 and "Traceback" not in (r.stderr or "")
    print(f"  => 受控退出判定: {'PASS' if ro_ok else 'FAIL'}")
finally:
    for p in touched:
        if os.path.exists(p):
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)

print()
print("=" * 78)
final = (cross_hits == 0 and raw_tb_rounds == 0 and ro_ok)
print("【验证项7】RESULT =", "PASS" if final else "FAIL")
sys.exit(0 if final else 1)
