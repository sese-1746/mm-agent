"""FR-5 端到端可运行率基线复现：CJK 文件名 vs ASCII 文件名。

- 真跑本机 Ollama（qwen3.5:latest），走真实链路 solve = generate → guard → run → repair → rerun
- 判据统一用 `runner.run_script` 的 `ok`（= 未超时 且 rc==0 且 manifest 声明的产物都存在）
- 只读真实数据；所有产物落 .agent-runs/2026-10-14/task-003/probe/，不污染 data/ 与 artifacts/
"""
from __future__ import annotations

import json
import shutil
import sys
import traceback
from pathlib import Path

REPO = Path(r"H:\mm-agent")
sys.path.insert(0, str(REPO))

from mmagent.agents.coder import (generate_script, make_regen_fn,  # noqa: E402
                                  make_runner)
from mmagent.tools import solve_tool                  # noqa: E402

DATASET = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
SRC_XLS = DATASET / "附件1 各门店客户满意度综合得分-A题.xls"
PROBE = REPO / ".agent-runs" / "2026-10-14" / "task-003" / "probe"
SCHEMA_REPORT = REPO / "artifacts" / "intake" / "schema_report.json"
N_PER_MODE = 3
TIMEOUT_S = 150

BASE_SPEC = {
    "goal": "读入输入表，画出各门店月度满意度折线图并落盘",
    "required_outputs": ["figures/satisfaction_line.png", "manifest.json"],
    "notes": [
        "用 xlrd 打开 .xls",
        "列名与表头行以 data_schema 为准（不要按语义猜列名）",
        "matplotlib 用 Agg 后端，中文图题，savefig(dpi=300)",
        '末尾把产出清单写入 manifest.json（内容形如 {"files": ["figures/satisfaction_line.png"]}）',
        "不要用 try/except 吞掉异常后正常退出；失败必须以非 0 退出码结束",
    ],
}


def stage(mode: str, idx: int) -> tuple[Path, list[str]]:
    """为单次尝试准备干净工作区，返回 (workdir, 传给模型的输入相对路径列表)。

    mode='ascii'：走 solve_tool.stage_inputs 做 ASCII 化；
    mode='cjk'  ：保留原中文文件名（对照）。
    两者都注入 data_schema（来自 FR-2 体检产物）。
    """
    ws = PROBE / f"ws2_{mode}_{idx}"
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "data").mkdir(parents=True, exist_ok=True)
    if mode == "ascii":
        return ws, solve_tool.stage_inputs([SRC_XLS], ws)
    shutil.copy2(SRC_XLS, ws / "data" / SRC_XLS.name)
    return ws, [f"data/{SRC_XLS.name}"]


def classify(res: dict, code: str) -> str:
    """按 stderr 归因（顺序即优先级），最后才看 manifest 缺件。"""
    run = res.get("run") or {}
    if run.get("ok"):
        return "ok"
    if run.get("timed_out"):
        return "timeout"
    tail = str(run.get("stderr_tail", ""))
    if "ScriptGuardError" in tail or "静态守卫" in tail:
        return "guard_violation"
    if "No such file" in tail or "FileNotFoundError" in tail or "Errno 2" in tail:
        # 是不是把输入名写错了（CJK 空格 quirk）
        if "附件 1" in code or "附件1 " in code or " 1.xls" in code:
            return "filename_quirk"
        return "file_not_found_other"
    if "no attribute 'strip'" in tail or "Cell" in tail:
        return "xlrd_usage"
    sp = Path(res.get("script_path") or "")
    if not sp.parent.joinpath("manifest.json").exists():
        return "manifest_missing"
    return "other_python_error"


def run_once(mode: str, idx: int) -> dict:
    ws, inputs = stage(mode, idx)
    spec = dict(BASE_SPEC)
    spec["inputs"] = inputs
    # 关键：把 FR-2 体检出的真实 schema（表头行 + 真列名/canonical）交接给 coder
    spec["data_schema"] = solve_tool.table_schema_digest(
        SCHEMA_REPORT, "att1_satisfaction")
    runner = make_runner()

    def gen_fn(s: dict) -> dict:
        return generate_script(s, runner=runner)

    # 用 F5 新增的官方适配器（回传上一版完整源码 + 错误上下文）。
    # 与基线时那份内联适配器功能等价，故与基线数字可比。
    regen_fn = make_regen_fn(runner=runner, base_spec=spec)

    rec: dict = {"mode": mode, "idx": idx, "inputs": inputs}
    try:
        res = solve_tool.solve(spec, ws, gen_fn=gen_fn, regen_fn=regen_fn,
                               timeout_s=TIMEOUT_S, repair_rounds=1)
        code = Path(res["script_path"]).read_text(encoding="utf-8")
        rec.update({
            "ok": bool(res["run"].get("ok")),
            "rc": res["run"].get("returncode"),
            "repaired": res.get("repaired"),
            "kind": classify(res, code),
            "stderr_tail": str(res["run"].get("stderr_tail", ""))[-600:],
            "declared_outputs": res["generated"].get("declared_outputs"),
            "figures": sorted(p.name for p in (ws / "figures").glob("*")) if (ws / "figures").exists() else [],
            "manifest": (ws / "manifest.json").exists(),
            "script_path": res["script_path"],
        })
    except Exception as exc:  # 生成/守卫/修复失败
        rec.update({"ok": False, "kind": "exception",
                    "exception": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()[-600:]})
    return rec


def main() -> None:
    (PROBE / "baseline").mkdir(parents=True, exist_ok=True)
    print(f"输入源: {SRC_XLS.name}", flush=True)
    print(f"每模式尝试次数: {N_PER_MODE}\n", flush=True)
    records: list[dict] = []
    for mode in ("cjk", "ascii"):
        for i in range(N_PER_MODE):
            print(f"--- mode={mode} #{i} ---", flush=True)
            rec = run_once(mode, i)
            records.append(rec)
            print(f"    ok={rec.get('ok')} rc={rec.get('rc')} kind={rec.get('kind')} "
                  f"repaired={rec.get('repaired')} figures={rec.get('figures')}",
                  flush=True)
    out = PROBE / "baseline" / "att3_baseline.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 汇总 =====", flush=True)
    for mode in ("cjk", "ascii"):
        rs = [r for r in records if r["mode"] == mode]
        ok = sum(1 for r in rs if r.get("ok"))
        print(f"{mode:6s}: {ok}/{len(rs)} ok   失败分类="
              f"{[r.get('kind') for r in rs if not r.get('ok')]}", flush=True)
    print(f"\n明细: {out}", flush=True)


if __name__ == "__main__":
    main()
