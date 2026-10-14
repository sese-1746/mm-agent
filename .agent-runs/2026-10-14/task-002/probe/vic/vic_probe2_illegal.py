"""Vic 验证项2：非法裁定被拦 + 不触发 langgraph UnboundLocalError。

- API 层：resume(None) / resume({}) / 投错 gate → InvalidGateDecision，
  且 type(exc).__name__ != 'UnboundLocalError' 且 isinstance(exc, NameError) is False；
- 对抗性对照：绕过 _validate_decision，直连 langgraph 用 Command(resume=None)
  **复现** UnboundLocalError（证明守卫确有存在必要，仅作对照，不算缺陷）；
- CLI 层：非法裁定 returncode == 2，stderr 含明确原因。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
PY = sys.executable
sys.path.insert(0, REPO)

from mmagent.agents.coordinator import Coordinator, InvalidGateDecision  # noqa: E402
from mmagent.store.db import init_db  # noqa: E402
from langgraph.types import Command  # noqa: E402

TMP = os.path.join(HERE, "ws2")
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


def api_case(label, run_id, context, gate, decision):
    """独立 Coordinator，start 挂起后投非法裁定，断言异常类型。"""
    c = Coordinator(db_path=DB, ckpt_path=CKPT)
    try:
        st = c.start(run_id, context)
        print(f"--- {label}: start -> paused={st.paused} gate={st.gate}")
        try:
            c.resume(run_id, gate, decision)
            check(False, f"{label}: 应抛 InvalidGateDecision 但没有")
        except BaseException as exc:  # noqa: BLE001
            name = type(exc).__name__
            is_invalid = isinstance(exc, InvalidGateDecision)
            is_name_err = isinstance(exc, NameError)
            print(f"    raised: {name}: {str(exc)[:120]}")
            check(is_invalid, f"{label}: 抛 InvalidGateDecision")
            check(name != "UnboundLocalError", f"{label}: 类名 != UnboundLocalError（实际 {name}）")
            check(not is_name_err, f"{label}: not isinstance(exc, NameError)（实际 {is_name_err}）")
    finally:
        c.close()


print("=" * 78)
print("【验证项2a】API 层非法裁定")
print("=" * 78)
api_case("resume(None)", "bad-none", {"caliber_conflicts": True}, "caliber_conflict", None)
api_case("resume({})", "bad-empty", {"caliber_conflicts": True}, "caliber_conflict", {})
api_case("投错 gate(挂 sanity 投 caliber)", "bad-wronggate", {"sanity_blocking": True},
         "caliber_conflict", {"resolution": "use_mapping", "note": "n"})

print()
print("=" * 78)
print("【验证项2b】对抗性对照：绕过 _validate_decision 直连 langgraph Command(resume=None)")
print("=" * 78)
c = Coordinator(db_path=DB, ckpt_path=CKPT)
try:
    c.start("adv-none", {"caliber_conflicts": True})
    print("--- 直连 langgraph，跳过 _validate_decision，投 Command(resume=None)")
    try:
        c._graph.invoke(Command(resume=None), c._config("adv-none"))
        print("    (未抛异常 —— 与预期相反)")
        fails.append("对抗性对照：直接 langgraph 传 resume=None 未复现 UnboundLocalError")
    except BaseException as exc:  # noqa: BLE001
        name = type(exc).__name__
        print(f"    raised: {name}: {str(exc)[:160]}")
        print(f"    isinstance NameError = {isinstance(exc, NameError)}")
        if name == "UnboundLocalError":
            print("    => 复现成功：langgraph 1.2.15 对 resume=None 抛 UnboundLocalError，"
                  "证明 _validate_decision 守卫必要（对照项，不算缺陷）")
        else:
            print("    (注：未复现 UnboundLocalError —— 记为对照观察)")
finally:
    c.close()

print()
print("=" * 78)
print("【验证项2c】CLI 层非法裁定 → returncode == 2 + stderr 明确原因")
print("=" * 78)
cli_db = os.path.join(TMP, "cli_mm.db")
cli_ckpt = os.path.join(TMP, "cli_ckpt.db")
for f in (cli_db, cli_ckpt, cli_ckpt + "-wal", cli_ckpt + "-shm"):
    if os.path.exists(f):
        os.remove(f)


def cli(*args):
    return subprocess.run([PY, "-m", "mmagent.cli", *args], cwd=REPO,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


r = cli("db", "init", "--db", cli_db)
assert r.returncode == 0
r = cli("run", "start", "--run-id", "cli-bad", "--conflicts", "--ckpt", cli_ckpt, "--db", cli_db)
assert r.returncode == 0, r.stderr
print("start ->", r.stdout.strip().splitlines()[-1][:110])

cases = [
    ("resume=None(JSON null)", "caliber_conflict", "null"),
    ("空 dict {}", "caliber_conflict", "{}"),
    ("投错 gate", "pre_export_compliance", '{"decision":"approve","note":"n"}'),
]
for label, gate, dec in cases:
    r = cli("run", "resume", "--run-id", "cli-bad", "--gate", gate, "--decision", dec,
            "--ckpt", cli_ckpt, "--db", cli_db)
    print(f"--- CLI {label}: rc={r.returncode}  stderr={r.stderr.strip()[:150]}")
    check(r.returncode == 2, f"CLI {label}: returncode == 2（实际 {r.returncode}）")
    check(bool(r.stderr.strip()), f"CLI {label}: stderr 含明确原因")
    check("Traceback" not in (r.stderr or ""), f"CLI {label}: 无原始 traceback")

# 非法调用后仍有合法裁定可恢复 → 检查点未污染
r = cli("run", "resume", "--run-id", "cli-bad", "--gate", "caliber_conflict",
        "--decision", json.dumps({"resolution": "use_mapping", "note": "n"}),
        "--ckpt", cli_ckpt, "--db", cli_db)
check(r.returncode == 0, "非法裁定后合法裁定仍可恢复（检查点未污染）")

print()
if fails:
    print("【验证项2】RESULT = FAIL, 失败项:", fails)
    sys.exit(1)
print("【验证项2】RESULT = PASS")
sys.exit(0)
