"""产品级能力路由（第 31 轮）：把「后端已实现、但只能 CLI 跑」的 6 项能力暴露成 HTTP。

设计铁律：**薄封装**。每条路由 = 一个请求模型（字段与底层函数形参逐字对齐）+ 转发 +
原样返回底层 dict。路由层不重写任何业务逻辑（解析/装配/校验/查询都在各自模块里）。

| 方法 | 路径 | 转发目标 |
|---|---|---|
| POST | `/problem/parse` | `agents/archivist.py::parse_problem` |
| POST | `/plan/build` | `agents/planner.py::generate_plan` |
| POST | `/paper/build` | `tools/paper_tool.py::build_paper` |
| GET | `/progress/status` | `tools/progress_tool.py::progress_status` |
| POST | `/consistency/check` | `tools/consistency_tool.py::check_consistency` |
| GET | `/profile/{profile_id}/trace` | `store/field_trace.py::list_field_traces` |

错误码口径（沿用 `README.md §3.4` + `app.py` 的既有处理器，不另造错误体）：

- `KeyError`（档案/版本不存在）→ **404** `NOT_FOUND`；
- 请求参数非法 → **422** `VALIDATION_ERROR`（pydantic / `RequestValidationError` 处理器给）；
- `LLMCallError` 及其子类 → 显式错误码，**绝不落成 500 类名**（见 `_llm_http_error`）：
  `LLMBudgetExceeded` 用自己的 `error_code=LLM_BUDGET_EXCEEDED` / `http_status=499`，
  其余子类按类名映射到 `LLM_MISSING_API_KEY`(503) / `LLM_OUTPUT_TRUNCATED`(502) /
  `LLM_OUTPUT_SYNTAX_ERROR`(502) / `LLM_CALL_FAILED`(502)。

实现方式：把映射结果包成 `HTTPException(status, detail={"error_code", "message"})`，
由 `app.py::_http_handler` 的 `detail.get("error_code")` 泛化分支原样取用——**复用**既有
错误体（`{"error_code","message","detail","trace_id"}`），不在路由层拼响应体。

注册顺序：本 router 在 `app.py` 里 **include 在 `routes_profile` 之后**（`/profile/{profile_id}`
先注册）。Starlette 按注册顺序匹配，静态/更长路径必须靠前，否则会被短动态段吞掉
（`routes_profile.py` 顶部注释记录的实测事故）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..agents import archivist, planner
from ..agents.base import LLMCallError
from ..store import field_trace
from ..store.db import DEFAULT_DB_PATH
from ..tools import consistency_tool, paper_tool, progress_tool

router = APIRouter(prefix="/api/v1", tags=["capabilities"])

# 默认库路径按 POSIX 形态写进模型默认值（`data/mm.db`），避免 Windows 反斜杠出现在 API 文档里。
_DEFAULT_DB = DEFAULT_DB_PATH.as_posix()

# `LLMCallError` 子类 → (error_code, http_status)。基类与未登记子类落到 `_LLM_FALLBACK`。
# 与 `agents/base.py` 的类名一一对应；`LLMBudgetExceeded` 不在此表——它自带
# `error_code` / `http_status` 属性，优先取自有值（499，见该类的 docstring）。
_LLM_ERROR_TABLE = {
    "MissingAPIKeyError": ("LLM_MISSING_API_KEY", 503),
    "TruncatedOutputError": ("LLM_OUTPUT_TRUNCATED", 502),
    "LLMOutputSyntaxError": ("LLM_OUTPUT_SYNTAX_ERROR", 502),
}
_LLM_FALLBACK = ("LLM_CALL_FAILED", 502)


def _llm_http_error(exc: LLMCallError) -> HTTPException:
    """`LLMCallError` → 带明确 `error_code` 的 `HTTPException`（鸭子类型取值，不硬绑子类）。"""
    code = getattr(exc, "error_code", None)
    status = getattr(exc, "http_status", None)
    if code and status:
        return HTTPException(status_code=int(status),
                             detail={"error_code": str(code), "message": str(exc)})
    code, status = _LLM_ERROR_TABLE.get(type(exc).__name__, _LLM_FALLBACK)
    return HTTPException(status_code=status,
                         detail={"error_code": code, "message": str(exc)})


# ================================================================ FR-1 题面解析

class ProblemParseRequest(BaseModel):
    """`archivist.parse_problem(problem_text, *, runner, profile_id, actor, db_path)` 的请求体。

    `runner` **不暴露**：它只能由 CLI / 测试注入（真实 LLM runner 无法从 JSON 反序列化），
    缺省由底层 `archivist.make_runner(db_path=...)` 构造。
    """
    problem_text: str = Field(min_length=1)
    profile_id: str | None = None
    actor: str = "archivist"
    db_path: str = _DEFAULT_DB


@router.post("/problem/parse")
def parse_problem(req: ProblemParseRequest) -> dict:
    """题面 → 结构化子问题（三要素 + 原文引用 + 确定性后校验）；**不写库**。"""
    try:
        return archivist.parse_problem(req.problem_text, profile_id=req.profile_id,
                                       actor=req.actor, db_path=req.db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMCallError as exc:
        raise _llm_http_error(exc) from exc


# ================================================================ FR-3 方案卡片

class PlanBuildRequest(BaseModel):
    """`planner.generate_plan(problem_text, *, runner, profile_id, db_path, out_dir, run_id)` 的请求体。"""
    problem_text: str = Field(min_length=1)
    profile_id: str | None = None
    db_path: str = _DEFAULT_DB
    out_dir: str = planner.PLAN_ARTIFACT_DIR.as_posix()
    run_id: str | None = None


@router.post("/plan/build")
def build_plan(req: PlanBuildRequest) -> dict:
    """题面 → 方案卡片（≥3 候选方法），落 `artifacts/plan/<run_id>.json` 并登记 artifacts。"""
    try:
        return planner.generate_plan(req.problem_text, profile_id=req.profile_id,
                                     db_path=req.db_path, out_dir=req.out_dir,
                                     run_id=req.run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LLMCallError as exc:
        raise _llm_http_error(exc) from exc
    except OSError as exc:
        # 写盘失败（`planner.py:450-454` 的 mkdir / write_text）：不给明确 error_code 会落进
        # `app.py` 兜底成裸 500（error_code="OSError"），前端无法给处置；CLI 同故障是 exit 3
        # （`cli.py:1396-1398`）。写法照同文件 `paper/build` 的 `PAPER_WRITE_FAILED`。
        raise HTTPException(status_code=500, detail={
            "error_code": "PLAN_WRITE_FAILED",
            "message": f"方案卡片写盘失败：{exc}"}) from exc


# ================================================================ FR-7 论文装配

class PaperBuildRequest(BaseModel):
    """`paper_tool.build_paper(*, profile_id, version, figures_dir, results_dir, out_path, actor, db_path)`。"""
    profile_id: str
    version: str | int = "last"
    figures_dir: str | None = None
    results_dir: str | None = None
    out_path: str
    actor: str = "assembler"
    db_path: str = _DEFAULT_DB


@router.post("/paper/build")
def build_paper(req: PaperBuildRequest) -> dict:
    """按 11 章固定骨架把档案装配成 docx + sidecar manifest；档案/版本不存在 → 404。"""
    try:
        return paper_tool.build_paper(profile_id=req.profile_id, version=req.version,
                                      figures_dir=req.figures_dir,
                                      results_dir=req.results_dir,
                                      out_path=req.out_path, actor=req.actor,
                                      db_path=req.db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        # 写盘失败（底层 docstring 明示：「OSError : 写盘失败（CLI 转 exit 3）」）。
        # 不给明确 error_code 的话会落进 app.py 兜底成裸 500（error_code="OSError"），
        # 前端只能显示「500」，与统一错误体口径不符；CLI 侧对应退出码 3。
        raise HTTPException(status_code=500, detail={
            "error_code": "PAPER_WRITE_FAILED",
            "message": f"论文写盘失败：{exc}"}) from exc


# ================================================================ FR-8 进度与时间

@router.get("/progress/status")
def get_progress_status(db_path: str = _DEFAULT_DB,
                        deadline: str | None = None,
                        now: str | None = None,
                        profile_id: str | None = None) -> dict:
    """倒计时 / 无产出预警 / 不做清单 / 最短可交版本；库文件不存在 → 404。"""
    try:
        return progress_tool.progress_status(db_path=db_path, deadline=deadline,
                                             now=now, profile_id=profile_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:            # deadline/now 不是合法 ISO8601
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ================================================================ FR-10 一致性校验

class ConsistencyCheckRequest(BaseModel):
    """`consistency_tool.check_consistency(*, paper_docx, results_dir, sidecar, include_appendix, ignore_tokens)`。"""
    paper_docx: str
    results_dir: str
    sidecar: str | None = None
    include_appendix: bool = False
    ignore_tokens: list[str] = Field(default_factory=list)


@router.post("/consistency/check")
def check_consistency(req: ConsistencyCheckRequest) -> dict:
    """正文/表格数字 vs 结果表 CSV 的出处校验；文件缺失 → 404，sidecar 结构不符 → 422。"""
    try:
        return consistency_tool.check_consistency(
            paper_docx=req.paper_docx, results_dir=req.results_dir,
            sidecar=req.sidecar, include_appendix=req.include_appendix,
            ignore_tokens=tuple(req.ignore_tokens))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:            # sidecar 读不出 / 结构不符 / 格式非法
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ================================================================ FR-9 字段级溯源

# 必须由 `app.py` 在 `routes_profile`（含 `/profile/{profile_id}`）**之后** include。
@router.get("/profile/{profile_id}/trace")
def list_profile_trace(profile_id: str,
                       version: str | None = None,
                       field_path: str | None = None,
                       limit: int = Query(1000, ge=0),
                       db_path: str = _DEFAULT_DB) -> dict:
    """按档案版本/字段路径查字段级溯源行；档案或版本不存在 → 404。"""
    try:
        return field_trace.list_field_traces(profile_id, version, field_path,
                                             limit=limit, db_path=db_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:            # version 不是整数
        raise HTTPException(status_code=422, detail=str(exc)) from exc
