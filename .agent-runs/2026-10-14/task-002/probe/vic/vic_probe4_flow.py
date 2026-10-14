"""Vic 补充验证：裁定驱动控制流（F1 P0）与 halt 语义。

独立于测试文件，直接断言：
- pre_export_compliance reject → stage=HALTED（无 stage:export）
- sanity_blocking abort → stage=HALTED（无 stage:render / stage:assemble）
- sanity_blocking fix_and_rerun 两次 → 超 MAX_RERUN → HALTED
- approve/accept 正常路径 → DONE（log 末项 stage:export）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
sys.path.insert(0, REPO)

from mmagent.agents.coordinator import Coordinator  # noqa: E402
from mmagent.store.db import init_db  # noqa: E402

TMP = os.path.join(HERE, "ws4")
os.makedirs(TMP, exist_ok=True)
DB = os.path.join(TMP, "mm.db")
CKPT = os.path.join(TMP, "ckpt.db")
for f in (DB, CKPT, CKPT + "-wal", CKPT + "-shm"):
    if os.path.exists(f):
        os.remove(f)
init_db(DB)

fails = []


def check(cond, label):
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        fails.append(label)


def new_coord():
    return Coordinator(db_path=DB, ckpt_path=CKPT)


print("=" * 78)
print("【补充验证】裁定驱动控制流 / halt 语义")
print("=" * 78)

# ① reject → HALTED，无 export
c = new_coord()
try:
    c.start("f1-reject", {})
    st = c.resume("f1-reject", "pre_export_compliance", {"decision": "reject", "note": "披露不全"})
    print("① reject ->", st.stage, "halted=", st.halted, "done=", st.done)
    print("   log:", st.log)
    check(st.stage == "HALTED" and st.halted and not st.done, "reject → HALTED/halted/done=False")
    check("stage:export" not in st.log, "reject 后无 stage:export（不产出 export 产物）")
finally:
    c.close()

# ② abort → HALTED，无 render/assemble
c = new_coord()
try:
    c.start("f1-abort", {"sanity_blocking": True})
    st = c.resume("f1-abort", "sanity_blocking", {"decision": "abort", "note": "中止"})
    print("② abort ->", st.stage, "log:", st.log)
    check(st.stage == "HALTED", "abort → HALTED")
    check("stage:render" not in st.log and "stage:assemble" not in st.log,
          "abort 后无 render/assemble")
finally:
    c.close()

# ③ fix_and_rerun 两次 → 超 MAX_RERUN → HALTED
c = new_coord()
try:
    c.start("f1-rerun", {"sanity_blocking": True})
    st1 = c.resume("f1-rerun", "sanity_blocking", {"decision": "fix_and_rerun", "note": "1"})
    print("③ rerun#1 ->", st1.stage, "paused=", st1.paused, "gate=", st1.gate)
    check(st1.paused and st1.gate == "sanity_blocking", "首次 fix_and_rerun 回到 sanity 卡点")
    check(st1.log.count("rerun:solve") == 1, "首次回边重入 solve 1 次")
    st2 = c.resume("f1-rerun", "sanity_blocking", {"decision": "fix_and_rerun", "note": "2"})
    print("③ rerun#2 ->", st2.stage, "log:", st2.log)
    check(st2.stage == "HALTED", "第二次 fix_and_rerun 超限 → HALTED")
    check(st2.log.count("rerun:solve") == 1, "超限时不再重入 solve")
finally:
    c.close()

# ④ approve 正常路径 → DONE
c = new_coord()
try:
    c.start("f1-ok", {"caliber_conflicts": True, "sanity_blocking": True})
    c.resume("f1-ok", "caliber_conflict", {"resolution": "use_mapping", "note": "n"})
    c.resume("f1-ok", "sanity_blocking", {"decision": "accept", "note": "n"})
    st = c.resume("f1-ok", "pre_export_compliance", {"decision": "approve", "note": "n"})
    print("④ approve ->", st.stage, "done=", st.done, "log:", st.log)
    check(st.done and st.stage == "DONE", "approve → DONE")
    check(st.log[-1] == "stage:export", "DONE log 末项为 stage:export")
finally:
    c.close()

print()
if fails:
    print("【补充验证】RESULT = FAIL, 失败项:", fails)
    sys.exit(1)
print("【补充验证】RESULT = PASS")
sys.exit(0)
