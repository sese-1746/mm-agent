"""Vic 验证项1：真实「挂起 → os._exit(0) 硬杀 → 新进程重启 → 恢复」跑到 DONE。

- 每个步骤都是**独立子进程**；start/resume/status 走**真实 CLI**；
- 中间一步用 os._exit(0) 硬杀（不 close、不清理 WAL）；
- 用**原生 sqlite3** 直读检查点库做旁证（不经过应用层）；
- 一律用 probe/vic 下临时路径，并核对 data/mm.db、data/checkpoints.db 未被污染。
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
PY = sys.executable
TMP = os.path.join(HERE, "ws1")
os.makedirs(TMP, exist_ok=True)

DB = os.path.join(TMP, "mm.db")
CKPT = os.path.join(TMP, "ckpt.db")
for f in (DB, CKPT, CKPT + "-wal", CKPT + "-shm"):
    if os.path.exists(f):
        os.remove(f)

RUN = "vic-xp-1"
LEGAL_CALIBER = {"resolution": "keep_raw", "note": "保留原始口径"}
LEGAL_SANITY = {"decision": "accept", "note": "复核通过"}
LEGAL_COMPLIANCE = {"decision": "approve", "note": "合规确认"}


def dev_null_guard_paths():
    """data/ 下两个库的 md5 快照（防污染）。"""
    out = {}
    for p in (os.path.join(REPO, "data", "mm.db"),
              os.path.join(REPO, "data", "checkpoints.db")):
        if os.path.exists(p):
            out[p] = hashlib.md5(open(p, "rb").read()).hexdigest()
        else:
            out[p] = "<absent>"
    return out


def cli(*args, label=""):
    r = subprocess.run([PY, "-m", "mmagent.cli", *args], cwd=REPO,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"--- {label} | argv: run {' '.join(args)}")
    print(f"    pid? rc={r.returncode}")
    if r.stdout.strip():
        print("    stdout:", r.stdout.strip().splitlines()[-1])
    if r.stderr.strip():
        print("    stderr:", r.stderr.strip()[:400])
    return r


def native_sqlite_inspect(ckpt):
    """原生 sqlite3 直读检查点库（旁证：挂起态来自持久化）。"""
    conn = sqlite3.connect(ckpt)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        print("    [sqlite] tables =", tables)
        for t in tables:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"    [sqlite] {t}: {n} rows")
            except sqlite3.Error as e:
                print(f"    [sqlite] {t}: <{e}>")
        # 直接读该 thread 的检查点主键（title 行为 thread_id）
        if "checkpoints" in tables:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(checkpoints)").fetchall()]
            print("    [sqlite] checkpoints cols =", cols)
            rows = conn.execute(
                "SELECT thread_id, checkpoint_id FROM checkpoints WHERE thread_id=? "
                "ORDER BY rowid", (RUN,)).fetchall()
            print(f"    [sqlite] 直读 thread_id={RUN!r} 的检查点行数 = {len(rows)}")
            for tid, cid in rows:
                print(f"    [sqlite]   checkpoint_id={cid}")
    finally:
        conn.close()


def main():
    print("=" * 78)
    print("【验证项1】跨进程恢复 + os._exit(0) 硬杀 + 原生 sqlite3 旁证")
    print("=" * 78)
    before = dev_null_guard_paths()
    print(f"[env] repo={REPO}")
    print(f"[env] ws={TMP}")
    r = cli("db", "init", "--db", DB, label="P0 db init")
    assert r.returncode == 0

    # 进程1：真实 CLI start。--conflicts --sanity-blocking → 挂 caliber_conflict
    p1 = cli("run", "start", "--run-id", RUN, "--conflicts", "--sanity-blocking",
             "--ckpt", CKPT, "--db", DB, label="P1 start (挂起 caliber)")
    assert p1.returncode == 0
    s1 = json.loads(p1.stdout.strip().splitlines()[-1])
    print("    =>", json.dumps({k: s1[k] for k in ("paused", "gate", "stage", "done")},
                               ensure_ascii=False))

    # 硬杀进程：投 caliber 裁定后 os._exit(0)（不 close、不清理 WAL）
    hk = subprocess.run([PY, os.path.join(HERE, "vic_hardkill_resume.py"),
                         DB, CKPT, RUN, "caliber_conflict", json.dumps(LEGAL_CALIBER)],
                        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    print(f"--- P2 HARDKILL (os._exit(0) after resume caliber) | rc={hk.returncode}")
    for line in hk.stdout.splitlines():
        if line.startswith("__PID__=") or line.startswith("__RESUME__="):
            print("   ", line)
    assert hk.returncode == 0
    st_hk = json.loads([l for l in hk.stdout.splitlines() if l.startswith("__RESUME__=")][0]
                       .split("=", 1)[1])
    print("    => hardkilled process left stage=", st_hk["stage"],
          "paused=", st_hk["paused"], "gate=", st_hk["gate"])
    assert (st_hk["paused"], st_hk["gate"]) == (True, "sanity_blocking")

    # 原生 sqlite3 直读 —— 证明挂起态来自持久化
    print("--- P2b native sqlite3 直读检查点库（旁证）")
    native_sqlite_inspect(CKPT)

    # 进程3：全新进程 status，仅凭同一 --ckpt 读回挂起态
    p3 = cli("run", "status", "--run-id", RUN, "--ckpt", CKPT, "--db", DB,
             label="P3 status (新进程读盘)")
    assert p3.returncode == 0
    s3 = json.loads(p3.stdout.strip().splitlines()[-1])
    assert (s3["paused"], s3["gate"]) == (True, "sanity_blocking"), s3
    print("    => 新进程读回: paused=%s gate=%s stage=%s" % (s3["paused"], s3["gate"], s3["stage"]))

    # 进程4/5：真实 CLI 续跑 sanity → compliance → DONE
    p4 = cli("run", "resume", "--run-id", RUN, "--gate", "sanity_blocking",
             "--decision", json.dumps(LEGAL_SANITY), "--ckpt", CKPT, "--db", DB,
             label="P4 resume sanity=accept")
    assert p4.returncode == 0
    s4 = json.loads(p4.stdout.strip().splitlines()[-1])
    assert (s4["paused"], s4["gate"]) == (True, "pre_export_compliance"), s4

    p5 = cli("run", "resume", "--run-id", RUN, "--gate", "pre_export_compliance",
             "--decision", json.dumps(LEGAL_COMPLIANCE), "--ckpt", CKPT, "--db", DB,
             label="P5 resume compliance=approve")
    assert p5.returncode == 0
    s5 = json.loads(p5.stdout.strip().splitlines()[-1])
    assert (s5["done"], s5["paused"], s5["stage"]) == (True, False, "DONE"), s5
    assert "stage:export" in s5["log"], s5["log"]
    print("    => 终态:", json.dumps({k: s5[k] for k in ("done", "paused", "stage", "halted")},
                                     ensure_ascii=False))
    print("    => log:", s5["log"])

    # 进程6：终态 status（新进程）
    p6 = cli("run", "status", "--run-id", RUN, "--ckpt", CKPT, "--db", DB,
             label="P6 status (终态)")
    s6 = json.loads(p6.stdout.strip().splitlines()[-1])
    assert (s6["done"], s6["stage"]) == (True, "DONE")
    assert "stage:export" in s6["log"]

    # 污染核对
    after = dev_null_guard_paths()
    print("--- data/ 污染核对")
    for k in before:
        mark = "OK(未变)" if before[k] == after[k] else "!!!变化!!!"
        print(f"    {k}: {mark}  {before[k]} -> {after[k]}")

    print("【验证项1】RESULT = PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
