"""求解相关路由（守卫 + 执行 + 「一句话任务描述 → 自动 spec → 生成并运行」）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agents import coder
from ..agents.base import LLMCallError
from ..pipeline.guard import ScriptGuardError
from ..tools import solve_tool
from . import queue_gate
# `LLMCallError` 的 HTTP 映射口径**复用**产品级能力路由那一份（不另造错误码表）：
# `LLM_BUDGET_EXCEEDED` 499 / `LLM_MISSING_API_KEY` 503 / 其余 502，绝不落成 500 类名。
from .routes_capabilities import _llm_http_error
from .routes_upload import _display

router = APIRouter(prefix="/api/v1", tags=["solve"])

REPO_ROOT = Path(__file__).resolve().parents[2]
# `artifacts/specs/` 是「自动生成 spec.json」的落点；两个常量可在测试中 monkeypatch
SPECS_DIR = REPO_ROOT / "artifacts" / "specs"
WORK_ROOT = REPO_ROOT / "artifacts" / "work"


class SolveRequest(BaseModel):
    """脚本执行请求体。"""
    script_path: str
    workdir: str
    timeout_s: int = 300


class SolveGenerateRequest(BaseModel):
    """「我要做什么」→ 自动 spec → 生成并运行的请求体。"""
    problem: str = Field(min_length=1)
    workdir: str | None = None
    inputs: list[str] = Field(default_factory=list)
    header_row: int | None = None
    profile_id: str | None = None
    max_attempts: int = 2
    timeout_s: int = 300


def _bad(status: int, code: str, message: str) -> HTTPException:
    """统一错误体入口（`detail.error_code` 由 `app.py::_http_handler` 取用）。"""
    return HTTPException(status_code=status,
                         detail={"error_code": code, "message": message})


@router.post("/solve/run")
def run_solve(req: SolveRequest) -> dict:
    """先 AST 守卫再沙箱执行脚本；守卫违规返回 422。

    执行体套进程内并发闸：满额 429 `QUEUE_FULL`（见 `api/queue_gate.py`）。
    """
    with queue_gate.long_task_slot():
        try:
            return solve_tool.validate_and_run(req.script_path, req.workdir,
                                               timeout_s=req.timeout_s)
        except ScriptGuardError as exc:
            raise HTTPException(status_code=422,
                                detail={"violations": exc.violations}) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/solve/generate")
def generate_and_solve(req: SolveGenerateRequest) -> dict:
    """任务描述 → 拼 `task_spec` → 落盘 `artifacts/specs/<run_id>.json` → 生成/守卫/执行/修复。

    `ok` 的语义 = `run.ok and dod.ok`（与 `cli solve` 退出码口径一致）；`run` 与 `dod`
    **两个对象都原样回传**，便于前端分别提示（副产物齐但脚本报错 ≠ 脚本干净但缺产物）。

    `gen_fn`/`regen_fn` 共用**同一个** runner 实例（照 `cli.py:1068-1077`）：裸函数会自建
    runner → 新 run_id → `metric_obs.run_id` 把一个 run 的预算/延迟拆成多份。
    """
    # `Field(min_length=1)` 挡不住纯空白（`"   "`）→ 去空白后仍要非空，否则 422。
    # 这里手判而不写 pydantic validator：validator 抛的 ValueError 会进 `ctx`，
    # 被 `app.py` 的 `_validation_handler` 原样 json 序列化时炸成 500。
    if not req.problem.strip():
        raise _bad(422, "VALIDATION_ERROR", "problem 不能为空白：请描述本次求解要做什么")

    run_id = uuid.uuid4().hex
    workdir = Path(req.workdir) if req.workdir \
        else WORK_ROOT / (req.profile_id or run_id[:8])

    task_spec: dict = {"problem": req.problem}
    if req.header_row is not None:
        task_spec["header_row"] = req.header_row

    with queue_gate.long_task_slot():
        try:
            staged = solve_tool.stage_inputs(req.inputs, workdir) if req.inputs else []
            task_spec["inputs"] = staged
            # 原始附件名（非 staging 后的 att1.xls）供 schema 装配推导 table_id
            if not task_spec.get("source_file") and req.inputs:
                task_spec["source_file"] = str(req.inputs[0])

            spec_file = SPECS_DIR / f"{run_id}.json"
            SPECS_DIR.mkdir(parents=True, exist_ok=True)
            spec_file.write_text(
                json.dumps(task_spec, ensure_ascii=False, indent=2), encoding="utf-8")

            runner_obj = coder.make_runner(run_id=run_id)
            regen = coder.make_regen_fn(runner=runner_obj, base_spec=task_spec)

            def gen_fn(spec: dict) -> dict:
                """绑定同一 runner 的 gen_fn（裸函数会自建 runner → 预算计数散架）。"""
                return coder.generate_script(spec, runner=runner_obj)

            result = solve_tool.solve(
                task_spec, workdir,
                gen_fn=gen_fn, regen_fn=regen,
                timeout_s=req.timeout_s,
                repair_rounds=max(0, req.max_attempts - 1))
        except FileNotFoundError as exc:      # inputs 指向不存在的文件
            # `stage_inputs` 抛的 FileNotFoundError 消息本身已是「输入文件不存在：<路径>」，
            # 再包一层会变成「输入文件不存在：输入文件不存在：…」（双否，task-002 §3-8）。
            # 故只在缺前缀时补一次，保证该短语在 message 里**恰好出现一次**。
            msg = str(exc)
            if "输入文件不存在" not in msg:
                msg = f"输入文件不存在：{exc.filename or msg}"
            raise _bad(404, "INPUT_NOT_FOUND", msg) from exc
        except ScriptGuardError as exc:
            raise HTTPException(status_code=422,
                                detail={"violations": exc.violations}) from exc
        except LLMCallError as exc:
            raise _llm_http_error(exc) from exc
        except coder.ScriptGenerationError as exc:
            # 与 422 SCRIPT_GUARD_VIOLATION **不是同一件事**：守卫违规是「脚本被守卫挡下，
            # violations 能指出哪一行违规」；本类是「两次生成都没产出可守卫的脚本」，
            # violations 必然为空。混用会让前端把「模型没产出」误显示成「脚本有安全问题」。
            # 也不能漏成 500 类名（`routes_capabilities.py` 的既有口径：绝不落成 500 类名）。
            raise _bad(502, "SCRIPT_GENERATION_FAILED",
                       "模型两次生成都没产出可执行的脚本（非静态守卫违规）："
                       f"{str(exc)[:200]}") from exc
        except OSError as exc:                # 写盘失败（spec / 产物）
            raise _bad(400, "WRITE_FAILED", f"求解写盘失败：{exc}") from exc

    run = result["run"]
    dod_report = result.get("dod") or {}
    return {
        "ok": bool(run.get("ok")) and bool(dod_report.get("ok")),
        "spec_path": _display(spec_file),
        "workdir": _display(workdir),
        "script_path": result.get("script_path"),
        "run": run,
        "dod": dod_report,
        "data_schema_status": result.get("data_schema_status"),
        "warnings": result.get("warnings", []),
    }
