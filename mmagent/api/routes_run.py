"""人机回环（HITL）路由：start / resume / status。

三者响应恒为 `RunStatus.to_dict()` 的**固定 11 键**：
`run_id / stage / paused / gate / question / done / halted / review / log /
plan_candidates_missing / export_skipped`（新增键置于末尾，与 `RunStatus` dataclass
字段顺序一致）。

`plan_candidates_missing`（第 27 轮新增）：PLAN 卡点因「库里没有 `kind='plan'` 产物」而
被跳过时为 `True`；否则 `False`。它回答的是「**为什么没让我确认选型**」——不暴露这个键，
用户只会看到流水线跳过了卡点却找不到任何解释。

`export_skipped`（第 28 轮新增 / D-93）：走到 export 节点但 `context` 未给
`export_params` 时为 `True`（于是**什么都没导出**却 `stage=DONE`）。它回答的是
「**这次跑完，交付目录到底生成了没有**」——不暴露这个键，`done=True` 会被读成
「已经导出好了」。

错误码映射（README §3.4）：
- `UnknownRunError`      → 404 `RUN_NOT_FOUND`
- `InvalidGateDecision`  → 409 `INVALID_GATE_DECISION`
- `CheckpointUnavailable`→ 503 `CHECKPOINT_UNAVAILABLE`
- `ExportParamOutOfRoot` → 400 `EXPORT_PARAM_OUT_OF_ROOT`（D-50，仅 `/run/start`）

为把业务 `error_code` 带出，`HTTPException.detail` 传 dict；`mmagent/app.py::_http_handler`
已泛化为「`detail` 是 dict 且含 `error_code` → 取之」（既有 4 分支行为保留）。
"""
from __future__ import annotations

import ntpath
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..agents.coordinator import (DEFAULT_CKPT_PATH, CheckpointUnavailable,
                                  Coordinator, InvalidGateDecision, UnknownRunError)
from ..store.db import DEFAULT_DB_PATH

router = APIRouter(prefix="/api/v1", tags=["run"])

#: D-50 允许根：仓库根（`H:\\mm-agent`）。`export_params` 的路径必须落在其内。
EXPORT_ROOT = Path(__file__).resolve().parents[2]

#: 本轮做落点校验的导出参数（其余参数不动）。
EXPORT_PATH_PARAMS = ("paper_docx", "out_dir")


class ExportParamOutOfRoot(ValueError):
    """`export_params` 里的路径形态非法、越出允许根，或（仅 `out_dir`）等于允许根本身。

    **不做静默夹紧**：一律抛本异常 → 路由转 400 `EXPORT_PARAM_OUT_OF_ROOT`，
    免得用户以为写到了 A、实际落在 B。

    `kind` 是给调用方区分的机器标签（`detail.reason`），不另开业务码：规格已把
    `EXPORT_PARAM_OUT_OF_ROOT` 冻结为「导出路径不被允许」的唯一业务码，只识别该码的
    调用方不该因为「指向根」而漏判；细分靠 `kind`（`at_root` / `outside` / `dotdot`…）。
    """

    error_code = "EXPORT_PARAM_OUT_OF_ROOT"

    def __init__(self, param: str, value, resolved: str | None, reason: str,
                 kind: str = "outside") -> None:
        super().__init__(f"export_params.{param}={value!r} {reason}")
        self.param = param
        self.value = value
        self.resolved = resolved
        self.reason = reason
        self.kind = kind


def _best_effort_abs(text: str) -> str | None:
    """尽量给出「解析后的绝对路径」（相对路径按仓库根解析），仅用于错误体回显。"""
    try:
        candidate = Path(text)
        base = candidate if candidate.is_absolute() else EXPORT_ROOT / candidate
        return str(base.resolve())
    except (OSError, ValueError):
        return None


def _shape_reason(text: str) -> tuple[str, str] | None:
    """形态检查：命中则返回 `(kind, 拒绝原因)`，`None` 表示可进入落点检查。

    判定依据是**字面形态**（前缀 / 盘符 / 段），不做路径解析——`Path("file:///C:/x")`
    在 Windows 上 `resolve()` 会得到 `C:Windows\\...` 这类不可靠串，拿解析结果当判据会误判。
    """
    raw = text.strip()
    if not raw:
        return ("empty", "为空路径")
    if raw.lower().startswith("file:"):
        return ("scheme", "不接受 file:// 前缀")
    if raw.startswith("~"):
        return ("tilde", "不接受 ~ 展开")
    if raw.startswith(("\\\\", "//")):
        return ("unc", "不接受 UNC 路径")
    drive, _rest = ntpath.splitdrive(raw)
    if drive:
        if not ntpath.isabs(raw):
            return ("drive_relative", "不接受盘符相对路径（如 C:foo）")
        if drive.rstrip(":").upper() != EXPORT_ROOT.drive.rstrip(":").upper():
            return ("drive", f"不接受盘符跳转（当前盘 {EXPORT_ROOT.drive}）")
    if ".." in raw.replace("\\", "/").split("/"):
        return ("dotdot", "不接受 .. 穿越")
    return None


def _within_root(path: Path) -> bool:
    """`path` 是否落在允许根内（含根自身）；`path` 须已 `resolve()`。"""
    try:
        path.relative_to(EXPORT_ROOT)
    except ValueError:
        return False
    return True


def check_export_params(export_params) -> dict[str, Path]:
    """校验 `export_params.paper_docx` / `export_params.out_dir`（存在才校验）。

    - 允许根 = 仓库根 `EXPORT_ROOT` 及其**所有子目录**；
    - 相对路径按仓库根解析；绝对路径必须落在根内；
    - 拒绝 `..` 段 / 盘符跳转 / UNC / `~` / `file://`；
    - `out_dir` 必须是允许根的**真子目录**（`"."` / 仓库根自身 → 拒绝：导出会清理根下的
      `figures/results/compliance`，那是一次导出顺手删了别人的产物）；`paper_docx` 只是输入
      文件，不受「真子目录」限制；
    - **不改写入参**：合法值原样交给下游，导出逻辑一行不动；越界抛
      `ExportParamOutOfRoot`（绝不静默夹紧）。

    返回 `{参数名: 解析后的绝对路径}`（仅供调用方留痕/断言，不回写请求体）。
    """
    resolved: dict[str, Path] = {}
    if not isinstance(export_params, dict):
        return resolved
    for param in EXPORT_PATH_PARAMS:
        raw = export_params.get(param)
        if raw is None:
            continue
        if not isinstance(raw, (str, Path)):
            raise ExportParamOutOfRoot(param, raw, None, "必须是字符串路径",
                                       kind="type")
        text = str(raw)
        absolute_str = _best_effort_abs(text)
        shape = _shape_reason(text)
        if shape:
            kind, why = shape
            raise ExportParamOutOfRoot(param, text, absolute_str, f"被拒绝：{why}",
                                       kind=kind)
        candidate = Path(text)
        by_root = (candidate if candidate.is_absolute()
                   else EXPORT_ROOT / candidate).resolve()
        # 相对路径真正落盘时按**进程 CWD** 解析（导出逻辑不改），故两种口径都必须
        # 落在根内；否则同一个串会「校验放行、写盘出界」。
        for path, how in ((by_root, "按仓库根解析"),
                          (candidate.resolve(), "按进程 CWD 解析")):
            if not _within_root(path):
                raise ExportParamOutOfRoot(
                    param, text, str(path),
                    f"越出允许根 {EXPORT_ROOT}（{how}；拒绝静默夹紧，请改用根内路径）",
                    kind="outside")
        if param == "out_dir" and by_root == EXPORT_ROOT:
            raise ExportParamOutOfRoot(
                param, text, str(by_root),
                f"等于允许根 {EXPORT_ROOT} 自身；out_dir 必须是根内的真子目录"
                f"（导出会清理目标下的 figures/results/compliance）",
                kind="at_root")
        resolved[param] = by_root
    return resolved


class RunStartRequest(BaseModel):
    """启动一次运行的请求体。"""
    run_id: str
    context: dict = {}
    db: str | None = None
    ckpt: str | None = None


class RunResumeRequest(BaseModel):
    """投递人工裁定的请求体。"""
    run_id: str
    gate: str
    decision: dict
    db: str | None = None
    ckpt: str | None = None


def _http_error(status_code: int, error_code: str, exc: Exception) -> HTTPException:
    """构造带业务 `error_code` 的 HTTPException（由 app 的统一处理器取用）。"""
    return HTTPException(status_code=status_code,
                         detail={"error_code": error_code, "message": str(exc)})


def _open(request: Request, db: str | None, ckpt: str | None) -> Coordinator:
    """打开协调器；检查点库不可用 → 503 `CHECKPOINT_UNAVAILABLE`。

    库路径优先级：请求体显式给 → `app.state` 服务级默认（`cli serve --db/--ckpt`）
    → 模块默认值。
    """
    db = db or getattr(request.app.state, "default_db", None) or DEFAULT_DB_PATH
    ckpt = ckpt or getattr(request.app.state, "default_ckpt", None) or DEFAULT_CKPT_PATH
    try:
        return Coordinator(db_path=db, ckpt_path=ckpt)
    except CheckpointUnavailable as exc:
        raise _http_error(503, "CHECKPOINT_UNAVAILABLE", exc) from exc


@router.post("/run/start")
def run_start(req: RunStartRequest, request: Request) -> dict:
    """启动一次运行；命中人工卡点属**正常状态**（`paused=True`）。

    `context.export_params` 的 `paper_docx` / `out_dir` 会被直接用来写盘，
    故先过 D-50 白名单（越界 / `out_dir` 指向仓库根自身 → 400
    `EXPORT_PARAM_OUT_OF_ROOT`，`detail.kind` 区分 `outside` / `at_root`），合法值原样透传。
    """
    try:
        check_export_params(req.context.get("export_params"))
    except ExportParamOutOfRoot as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": exc.error_code, "message": str(exc),
                    "param": exc.param, "value": exc.value,
                    "resolved": exc.resolved, "kind": exc.kind,
                    "allowed_root": str(EXPORT_ROOT)}) from exc
    coord = _open(request, req.db, req.ckpt)
    try:
        status = coord.start(req.run_id, req.context)
    except CheckpointUnavailable as exc:
        raise _http_error(503, "CHECKPOINT_UNAVAILABLE", exc) from exc
    finally:
        coord.close()
    return status.to_dict()


@router.post("/run/resume")
def run_resume(req: RunResumeRequest, request: Request) -> dict:
    """投递人工裁定并从挂起点恢复；非法裁定 → 409。"""
    coord = _open(request, req.db, req.ckpt)
    try:
        status = coord.resume(req.run_id, req.gate, req.decision)
    except InvalidGateDecision as exc:
        raise _http_error(409, "INVALID_GATE_DECISION", exc) from exc
    except CheckpointUnavailable as exc:
        raise _http_error(503, "CHECKPOINT_UNAVAILABLE", exc) from exc
    finally:
        coord.close()
    return status.to_dict()


@router.get("/run/status")
def run_status(request: Request, run_id: str, db: str | None = None,
               ckpt: str | None = None) -> dict:
    """读取当前快照（不推进图）；线程不存在 → 404。"""
    coord = _open(request, db, ckpt)
    try:
        status = coord.status(run_id)
    except UnknownRunError as exc:
        raise _http_error(404, "RUN_NOT_FOUND", exc) from exc
    except CheckpointUnavailable as exc:
        raise _http_error(503, "CHECKPOINT_UNAVAILABLE", exc) from exc
    finally:
        coord.close()
    return status.to_dict()
