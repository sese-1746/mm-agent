"""队长独立端到端抽查（C 项，精简版）：CJK / ASCII 各 1 次。

走真实链路 solve_tool.solve(gen_fn=generate_script, regen_fn=make_regen_fn)，
判据 = runner.run_script 的 ok。产物落 probe/lead_c/，不污染 data/、artifacts/。
注意：本抽查与其余 2~3 个执行体**并发**运行（Ollama 单机串行），数字须带此前提解读。
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
from mmagent.tools import solve_tool  # noqa: E402

DATASET = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
SRC_XLS = DATASET / "附件1 各门店客户满意度综合得分-A题.xls"
PROBE = REPO / ".agent-runs" / "2026-10-14" / "task-003" / "probe" / "lead_c"
SCHEMA_REPORT = REPO / "artifacts" / "intake" / "schema_report.json"
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


def stage(mode: str, idx: int):
    ws = PROBE / f"lead_ws_{mode}_{idx}"
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "data").mkdir(parents=True, exist_ok=True)
    if mode == "ascii":
        return ws, solve_tool.stage_inputs([SRC_XLS], ws)
    shutil.copy2(SRC_XLS, ws / "data" / SRC_XLS.name)
    return ws, [f"data/{SRC_XLS.name}"]


def run_once(mode: str, idx: int) -> dict:
    ws, inputs = stage(mode, idx)
    spec = dict(BASE_SPEC)
    spec["inputs"] = inputs
    spec["data_schema"] = solve_tool.table_schema_digest(SCHEMA_REPORT, "att1_satisfaction")
    runner = make_runner()
    gen_fn = lambda s: generate_script(s, runner=runner)          # noqa: E731
    regen_fn = make_regen_fn(runner=runner, base_spec=spec)
    rec = {"mode": mode, "idx": idx, "inputs": inputs}
    try:
        res = solve_tool.solve(spec, ws, gen_fn=gen_fn, regen_fn=regen_fn,
                               timeout_s=TIMEOUT_S, repair_rounds=1)
        rec.update({
            "ok": bool(res["run"].get("ok")),
            "rc": res["run"].get("returncode"),
            "repaired": res.get("repaired"),
            "manifest_present": res["run"].get("manifest_present"),
            "stderr_tail": str(res["run"].get("stderr_tail", ""))[-400:],
            "figures": sorted(p.name for p in (ws / "figures").glob("*"))
            if (ws / "figures").exists() else [],
            "script_path": res["script_path"],
        })
    except Exception as exc:  # noqa: BLE001
        rec.update({"ok": False, "kind": "exception",
                    "exception": f"{type(exc).__name__}: {exc}"[:400],
                    "trace": traceback.format_exc()[-400:]})
    return rec


def main() -> None:
    PROBE.mkdir(parents=True, exist_ok=True)
    print(f"schema_report 存在: {SCHEMA_REPORT.exists()}", flush=True)
    recs = []
    for mode in ("cjk", "ascii"):
        for i in range(1):
            print(f"--- mode={mode} #{i} ---", flush=True)
            r = run_once(mode, i)
            recs.append(r)
            print(f"    ok={r.get('ok')} rc={r.get('rc')} repaired={r.get('repaired')} "
                  f"figures={r.get('figures')} exc={r.get('exception')}", flush=True)
    (PROBE / "lead_c_result.json").write_text(
        json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved:", PROBE / "lead_c_result.json", flush=True)


if __name__ == "__main__":
    main()
