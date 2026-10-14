"""求解工具：生成 → 守卫 → 执行 → 单轮修复 的确定性串联。

依赖方向：tools 只调 pipeline（guard/runner/repair），**不 import agents**。
因此「生成脚本」与「重新生成」由上层（agents 层）以回调注入：
- gen_fn(task_spec) -> dict            ，约定返回 {"filename","code","declared_outputs",...}
- regen_fn(prev_src, error_context) -> str
`agents.coder.generate_script` 可直接作为 gen_fn；regen_fn 见 `make_regen_fn`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

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
