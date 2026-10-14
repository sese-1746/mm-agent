"""求解工具：生成 → 守卫 → 执行 → 单轮修复 的确定性串联。

依赖方向：tools 只调 pipeline（guard/runner/repair），**不 import agents**。
因此「生成脚本」与「重新生成」由上层（agents 层）以回调注入：
- gen_fn(task_spec) -> dict            ，约定返回 {"filename","code","declared_outputs",...}
- regen_fn(prev_src, error_context) -> str
`agents.coder.generate_script` 可直接作为 gen_fn；regen_fn 见 `make_regen_fn`。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, Sequence

from ..pipeline import guard, repair, runner


def validate_and_run(script_path: str | Path, workdir: str | Path,
                     timeout_s: int = 300) -> dict:
    """先做 AST 守卫再执行脚本。

    违规时抛 pipeline.guard.ScriptGuardError；否则返回 runner.run_script 的执行报告。
    """
    path = Path(script_path)
    guard.validate_script_ast(path.read_text(encoding="utf-8"))
    return runner.run_script(path, workdir, timeout_s=timeout_s)


def validate_only(src: str) -> None:
    """仅做静态守卫（供 API 预检脚本用），违规抛 ScriptGuardError。"""
    guard.validate_script_ast(src)


def solve(task_spec: dict, workdir: str | Path,
          gen_fn: Callable[[dict], dict] | None = None,
          regen_fn: Callable[[str, str], str] | None = None,
          timeout_s: int = 300, repair_rounds: int = 1) -> dict:
    """串起 generate → guard → run → （失败则）repair 一轮。

    返回 {"generated": {...}, "run": {...}, "repaired": bool, "script_path": str}。
    gen_fn 缺失时抛 ValueError（提示由上层注入 agents.coder.generate_script）。
    """
    if gen_fn is None:
        raise ValueError(
            "solve 需要注入 gen_fn（如 agents.coder.generate_script）；"
            "tools 层不 import agents，以保持依赖方向单向")

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    generated = gen_fn(task_spec)
    filename = generated.get("filename") or "solve.py"
    script_path = workdir / filename
    code = str(generated.get("code", ""))
    script_path.write_text(code, encoding="utf-8")
    repaired = False

    # 第一道闸：静态守卫。不过则同样走 repair 一轮（repair 内部含守卫复检）
    try:
        guard.validate_script_ast(code)
        report = runner.run_script(script_path, workdir, timeout_s=timeout_s)
    except guard.ScriptGuardError as exc:
        if regen_fn is None:
            raise
        code = repair.repair_once(code, "", regen_fn, max_rounds=repair_rounds)
        script_path.write_text(code, encoding="utf-8")
        report = runner.run_script(script_path, workdir, timeout_s=timeout_s)
        repaired = True

    # 第二道闸：执行失败（rc≠0 / 超时 / manifest 缺件）→ repair 一轮后重跑
    if not report["ok"] and regen_fn is not None and not repaired:
        code = repair.repair_once(code, report.get("stderr_tail", ""),
                                  regen_fn, max_rounds=repair_rounds)
        script_path.write_text(code, encoding="utf-8")
        report = runner.run_script(script_path, workdir, timeout_s=timeout_s)
        repaired = True

    return {"generated": generated, "run": report, "repaired": repaired,
            "script_path": str(script_path)}


# --------------------------------------------------------------------------
# task-003：把「输入稳健化」与「schema 交接」做成 solve 的前置工具
#
# 实测动机（.agent-runs/2026-10-14/task-003/baseline-repro.md）：
# ① 参赛附件的文件名是「附件1 各门店客户满意度综合得分-A题.xls」这种中文+空格，
#    LLM 生成脚本时**稳定地**插入多余空格（写作 `data/附件 1 …-A 题.xls`）→ FileNotFoundError。
# ② 附件1 的真实列名是 `月份门店` + `1.0`…`12.0`（月份列名就是数值），
#    表头还在第 1 行（第 0 行是合并大标题）—— 模型无从猜出，只会报「未找到包含'满意度'的列」。
#    FR-2 体检产物（schema_report.json / field_mapping.csv）里这些信息**都有**，此前没交接给 coder。
# --------------------------------------------------------------------------

_ASCII_SAFE = set("abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def stage_inputs(inputs: Sequence[str | Path], workdir: str | Path,
                 subdir: str = "data") -> list[str]:
    """把输入文件复制进 workdir 并改用**纯 ASCII 文件名**，返回模型可见的相对路径列表。

    纯 ASCII 且无空格的文件名保持原名；否则重命名为 `att1<后缀>`、`att2<后缀>`…
    （按输入顺序编号）。这样可消除「模型给中文名插空格」这一稳定失败模式。
    """
    workdir = Path(workdir)
    dest_dir = workdir / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged: list[str] = []
    for i, raw in enumerate(inputs, start=1):
        src = Path(raw)
        if not src.exists():
            raise FileNotFoundError(f"输入文件不存在：{src}")
        name = src.name
        if all(ch in _ASCII_SAFE for ch in name):
            alias = name
        else:
            alias = f"att{i}{src.suffix.lower()}"
        shutil.copy2(src, dest_dir / alias)
        staged.append(f"{subdir}/{alias}")
    return staged


def table_schema_digest(schema_report_path: str | Path,
                        table_id: str) -> dict[str, Any]:
    """从 FR-2 体检产物 `schema_report.json` 抽出可直接喂给 coder 的**精简 schema**。

    返回：
      table_id / source_file / sheet_name / header_row(0-based 真列名行) /
      data_start_row / title / columns[{name, raw, col, canonical, dtype}]
    用途：把它放进 task_spec（如 `spec["data_schema"]`），让 coder 知道**真实列名**，
    而不是按语义去猜（实测猜测必然失败）。
    """
    data = json.loads(Path(schema_report_path).read_text(encoding="utf-8"))
    tables = data["tables"] if isinstance(data, dict) else data
    table = next((t for t in tables if t.get("table_id") == table_id), None)
    if table is None:
        raise KeyError(f"schema_report 中找不到 table_id={table_id!r}")
    fields = [f for f in (data.get("fields") or [])
              if f.get("table_id") == table_id]
    header_rows = int(table.get("header_rows") or 1)
    return {
        "table_id": table_id,
        "source_file": table.get("source_file"),
        "sheet_name": table.get("sheet_name"),
        "title": table.get("table_title"),
        # 真列名所在行（0-based）：header_rows 是"表头占几行"，其最后一行才是列名行
        "header_row": max(0, header_rows - 1),
        "data_start_row": header_rows,
        "columns": [
            {"name": f.get("field_name"), "raw": f.get("field_raw"),
             "col": f.get("source_column"), "canonical": f.get("canonical"),
             "dtype": f.get("dtype")}
            for f in fields
        ],
        "note": ("列名以 columns[].name 为准（原始表头可能是 '1.0' 这类数值，"
                 "请用 columns[].col 或 columns[].name 定位，不要按语义猜列名）；"
                 "第 0 行通常是合并大标题，不是列名。"),
    }
