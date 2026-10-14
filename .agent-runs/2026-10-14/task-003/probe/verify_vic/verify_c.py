"""验证员 Vic — Part C：独立复算端到端可运行率。

复用真实链路 solve（generate → guard → run → repair → rerun）：
  gen_fn  = mmagent.agents.coder.generate_script
  regen_fn= mmagent.agents.coder.make_regen_fn
同一任务规格；CJK 原名 vs ASCII 化（solve_tool.stage_inputs）各 3 次。
判据统一 = pipeline.runner.run_script 的 `ok`（未超时 且 rc==0 且 manifest 声明产物齐全）。
逐次记录 ok/rc/kind/stderr 尾部/repaired/异常类型。产物落 probe/verify_vic/。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import traceback
from pathlib import Path

REPO = Path(r"H:\mm-agent")
sys.path.insert(0, str(REPO))

from mmagent.agents.coder import make_regen_fn, make_runner  # noqa: E402
from mmagent.agents.coder import generate_script  # noqa: E402
from mmagent.tools import solve_tool  # noqa: E402

DATASET = Path(r"C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题")
SRC_XLS = DATASET / "附件1 各门店客户满意度综合得分-A题.xls"
HERE = Path(__file__).parent
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
    ws = HERE / f"ws_c_{mode}_{idx}"
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "data").mkdir(parents=True, exist_ok=True)
    if mode == "ascii":
        return ws, solve_tool.stage_inputs([SRC_XLS], ws)
    shutil.copy2(SRC_XLS, ws / "data" / SRC_XLS.name)
    return ws, [f"data/{SRC_XLS.name}"]


def classify(run: dict, code: str) -> str:
    if run.get("ok"):
        return "ok"
    if run.get("timed_out"):
        return "timeout"
    tail = str(run.get("stderr_tail", ""))
    if "ScriptGuardError" in tail or "静态守卫" in tail or "守卫" in tail:
        return "guard_violation"
    if "No such file" in tail or "FileNotFoundError" in tail or "Errno 2" in tail:
        if "附件 1" in code or "附件1 " in code or " 1.xls" in code:
            return "filename_quirk"
        return "file_not_found_other"
    if "no attribute 'strip'" in tail or "Cell" in tail:
        return "xlrd_usage"
    if "manifest" in tail.lower() or not Path(run.get("_ws", ".")).joinpath(
            "manifest.json").exists():
        # 由调用方补 _ws；此处仅兜底
        pass
    if run.get("manifest_present") is False:
        return "manifest_missing"
    return "other_python_error"


def run_once(mode: str, idx: int) -> dict:
    ws, inputs = stage(mode, idx)
    spec = dict(BASE_SPEC)
    spec["inputs"] = inputs
    spec["data_schema"] = solve_tool.table_schema_digest(
        SCHEMA_REPORT, "att1_satisfaction")
    coder_runner = make_runner()
    gen_fn = lambda s: generate_script(s, runner=coder_runner)  # noqa: E731
    regen_fn = make_regen_fn(runner=coder_runner, base_spec=spec)

    rec: dict = {"mode": mode, "idx": idx, "inputs": inputs,
                 "ts": time.strftime("%H:%M:%S")}
    t0 = time.time()
    try:
        res = solve_tool.solve(spec, ws, gen_fn=gen_fn, regen_fn=regen_fn,
                               timeout_s=TIMEOUT_S, repair_rounds=1)
        code = Path(res["script_path"]).read_text(encoding="utf-8")
        run = res["run"]
        kind = classify(run, code)
        if kind == "other_python_error" and run.get("manifest_present") is False:
            kind = "manifest_missing"
        rec.update({
            "ok": bool(run.get("ok")),
            "rc": run.get("returncode"),
            "manifest_present": run.get("manifest_present"),
            "manifest_missing": run.get("manifest_missing"),
            "repaired": res.get("repaired"),
            "kind": kind,
            "stderr_tail": str(run.get("stderr_tail", ""))[-700:],
            "stdout_tail": str(run.get("stdout_tail", ""))[-300:],
            "figures": sorted(p.name for p in (ws / "figures").glob("*"))
            if (ws / "figures").exists() else [],
            "script_name": Path(res["script_path"]).name,
            "code_head": code[:300],
        })
    except Exception as exc:  # noqa: BLE001
        rec.update({"ok": False, "rc": None, "kind": "exception",
                    "exception": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()[-900:]})
    rec["elapsed_s"] = round(time.time() - t0, 1)
    return rec


def main() -> None:
    print(f"输入源: {SRC_XLS.name}", flush=True)
    records: list[dict] = []
    for mode in ("cjk", "ascii"):
        for i in range(N_PER_MODE):
            print(f"--- verify_vic mode={mode} #{i} ---", flush=True)
            rec = run_once(mode, i)
            records.append(rec)
            print(f"    ok={rec.get('ok')} rc={rec.get('rc')} kind={rec.get('kind')} "
                  f"repaired={rec.get('repaired')} elapsed={rec.get('elapsed_s')}s "
                  f"manifest={rec.get('manifest_present')}", flush=True)
    out = HERE / "verify_c_result.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 汇总（验证员 Vic 独立复算）=====", flush=True)
    for mode in ("cjk", "ascii"):
        rs = [r for r in records if r["mode"] == mode]
        ok = sum(1 for r in rs if r.get("ok"))
        print(f"{mode:6s}: {ok}/{len(rs)} ok   kinds={[r.get('kind') for r in rs]}",
              flush=True)
    print(f"\n明细: {out}", flush=True)


if __name__ == "__main__":
    main()
