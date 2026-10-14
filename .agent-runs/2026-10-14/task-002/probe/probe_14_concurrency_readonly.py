"""probe_14 (Max) — 修复轮 2 · F3 验收：
 (1) 8 轮「两个进程并发恢复同一挂起点」→ 跨台命中 = 0（合规卡点绝未被非法裁定跨过）
 (2) 检查点库只读 → 受控退出（exit 3，无原始 traceback）
"""
import json
import os
import stat
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
PY = sys.executable
LEGAL = {"resolution": "use_mapping", "note": "按建议口径合并"}


def cli(args, **kw):
    return subprocess.run([PY, "-m", "mmagent.cli", *args], cwd=REPO,
                          capture_output=True, text=True, **kw)


def decisions_of(ckpt, db, run_id):
    code = (
        "import json,sys;"
        "from mmagent.agents.coordinator import Coordinator;"
        f"c=Coordinator(db_path=r'{db}', ckpt_path=r'{ckpt}');"
        f"s=c._graph.get_state({{'configurable':{{'thread_id':'{run_id}'}}}});"
        "print(json.dumps(dict((s.values or {}).get('decisions', {})), ensure_ascii=False));"
        "c.close()"
    )
    r = subprocess.run([PY, "-c", code], cwd=REPO, capture_output=True, text=True)
    return json.loads(r.stdout.strip().splitlines()[-1])


print("======== (1) 8 轮并发恢复压测 ========")
cross_hits = 0
for i in range(8):
    d = tempfile.mkdtemp(prefix=f"race_{i}_")
    db = os.path.join(d, "mm.db")
    ckpt = os.path.join(d, "ckpt.db")
    r0 = cli(["db", "init", "--db", db])
    r0b = cli(["run", "start", "--run-id", f"r{i}", "--conflicts", "--db", db, "--ckpt", ckpt])
    assert r0b.returncode == 0, r0b.stderr
    resume = ["run", "resume", "--run-id", f"r{i}", "--gate", "caliber_conflict",
              "--decision", json.dumps(LEGAL, ensure_ascii=False), "--db", db, "--ckpt", ckpt]
    p1 = subprocess.Popen([PY, "-m", "mmagent.cli", *resume], cwd=REPO,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p2 = subprocess.Popen([PY, "-m", "mmagent.cli", *resume], cwd=REPO,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    o1, e1 = p1.communicate()
    o2, e2 = p2.communicate()
    dec = decisions_of(ckpt, db, f"r{i}")
    crossed = dec.get("pre_export_compliance")
    bad = crossed is not None and (crossed or {}).get("decision") not in {"approve", "reject"}
    raw_tb = ("Traceback" in e1) or ("Traceback" in e2)
    if bad:
        cross_hits += 1
    print(f" round {i}: rc=({p1.returncode},{p2.returncode}) decisions={dec} "
          f"跨台={'是' if bad else '否'} 原始traceback={'有' if raw_tb else '无'}")
print(f"==> 8 轮跨台命中 = {cross_hits}（期望 0）")

print("\n======== (2) 检查点库只读 → 受控退出 ========")
d2 = tempfile.mkdtemp(prefix="ro_")
db2 = os.path.join(d2, "mm.db")
ckpt2 = os.path.join(d2, "ckpt.db")
cli(["db", "init", "--db", db2])
cli(["run", "start", "--run-id", "ro-1", "--conflicts", "--db", db2, "--ckpt", ckpt2])
os.chmod(ckpt2, stat.S_IREAD)  # 只读
r = cli(["run", "resume", "--run-id", "ro-1", "--gate", "caliber_conflict",
         "--decision", json.dumps(LEGAL, ensure_ascii=False), "--db", db2, "--ckpt", ckpt2])
print("returncode =", r.returncode)
print("stderr =", (r.stderr or "").strip()[:300])
print("含原始 traceback?", "Traceback" in (r.stderr or ""))
os.chmod(ckpt2, stat.S_IWRITE)
print("\nprobe_14 完成")
