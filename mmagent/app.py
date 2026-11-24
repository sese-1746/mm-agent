"""FastAPI 应用工厂：挂载业务路由 + 统一错误体 + （可选）静态前端。

错误体固定结构（所有异常处理器一致）：
{"error_code": str, "message": str, "detail": any, "trace_id": str}

静态前端：`create_app(static_dir=...)` 在**目录存在**时把 `StaticFiles(html=True)`
挂到 `/`，且**必须挂在业务 router 之后**（Starlette 按注册顺序匹配，`/` 放最后
才不会吞掉 `/api/v1/*`）；目录不存在则跳过（保证 `import mmagent.app` 永不炸）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import (routes_archive, routes_artifacts, routes_audit,
                  routes_capabilities, routes_compliance, routes_evaluation, routes_figures,
                  routes_intake, routes_ops, routes_profile, routes_rag, routes_rag_qa,
                  routes_run, routes_solve, routes_upload, routes_review, sse)
from .security.desensitize import DesensitizeBlocked

API_PREFIX = "/api/v1"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATIC_DIR = REPO_ROOT / "frontend"


def _error_body(error_code: str, message: str, detail=None,
                trace_id: str | None = None) -> dict:
    """构造统一错误体。"""
    return {"error_code": error_code, "message": message, "detail": detail,
            "trace_id": trace_id or uuid.uuid4().hex}


def create_app(static_dir: str | Path | None = None) -> FastAPI:
    """创建并返回 FastAPI 应用。

    `static_dir` 默认 `<repo>/frontend`；**目录存在才**挂载静态站点到 `/`，
    且挂载发生在所有业务 router 之后；目录不存在则静默跳过。
    """
    app = FastAPI(title="mmagent", version="0.1.0",
                  description="数学建模竞赛协作 Agent（全本地）")

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict:
        """健康检查。"""
        return {"status": "ok", "service": "mmagent"}

    # 业务路由
    app.include_router(routes_intake.router)
    app.include_router(routes_solve.router)
    app.include_router(routes_profile.router)
    app.include_router(routes_compliance.router)
    app.include_router(routes_artifacts.router)
    app.include_router(routes_audit.router)
    app.include_router(routes_run.router)
    app.include_router(routes_figures.router)
    app.include_router(routes_ops.router)
    # FR-14 档案库（真题 PDF / 论文逐页图）：本地文件只读，零网络
    app.include_router(routes_archive.router)
    # 产品级能力薄封装（题面解析/方案卡片/论文装配/进度/一致性/字段溯源）。
    # **必须**在 `routes_profile` 之后 include：`/profile/{profile_id}/trace` 与
    # `/profile/{profile_id}` 同前缀，按注册顺序匹配（`routes_profile.py` 顶部注释的实测事故）。
    app.include_router(routes_capabilities.router)
    # 第 35 轮：上传（base64 JSON → `data/uploads/<profile_id>/` + PDF 文字层抽取）。
    # **静态路径**（无动态段），放在 `routes_capabilities` 之后不产生吞并风险。
    app.include_router(routes_upload.router)
    # 第 36 轮：RAG 向量检索（`/rag/search`，索引落 artifacts/rag/index/，不建表）。
    # 静态路径，与既有路由无前缀重叠。
    app.include_router(routes_rag.router)
    # 概念问答：检索同一个 RAG 索引，并在同一请求中接收可选图片。
    app.include_router(routes_rag_qa.router)
    # 文献综述：多个 .docx → 纯 Python 确定性综述 + 可编辑 Word 产物
    app.include_router(routes_review.router)
    # 论文与文献综述共用的确定性评测对照
    app.include_router(routes_evaluation.router)
    # SSE（规划书 §7.1 `POST /api/v1/stream/run`）：同样必须在静态挂载之前
    app.include_router(sse.router)

    # 静态前端（**必须最后挂载**）：目录存在才挂，避免吞掉 /api/v1/*
    target = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
    if target.is_dir():
        app.mount("/", StaticFiles(directory=str(target), html=True), name="static")

    # ---- 统一错误体 ----
    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request,
                            exc: StarletteHTTPException) -> JSONResponse:
        """HTTPException（含 404/422 脚本守卫违规）统一为固定错误体。"""
        detail = exc.detail
        if isinstance(detail, dict) and "violations" in detail:
            code, message = "SCRIPT_GUARD_VIOLATION", "脚本静态检查未通过"
        elif isinstance(detail, dict) and detail.get("error_code") == "DESENSITIZE_BLOCKED":
            code, message = "DESENSITIZE_BLOCKED", "内容命中未脱敏敏感信息，已阻断入库"
        elif isinstance(detail, dict) and detail.get("error_code"):
            # 泛化：detail 是 dict 且含 error_code → 取业务错误码（如 RUN_NOT_FOUND /
            # INVALID_GATE_DECISION / CHECKPOINT_UNAVAILABLE）。
            code = str(detail["error_code"])
            message = str(detail.get("message") or f"HTTP_{exc.status_code}")
        elif exc.status_code == 403:
            code, message = "DESENSITIZE_BLOCKED", "内容命中未脱敏敏感信息，已阻断入库"
        elif exc.status_code == 404:
            code, message = "NOT_FOUND", "资源不存在"
        elif exc.status_code == 422:
            code, message = "VALIDATION_ERROR", "请求参数校验失败"
        else:
            code, message = f"HTTP_{exc.status_code}", str(detail)
        return JSONResponse(status_code=exc.status_code,
                            content=_error_body(code, message, detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request,
                                  exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_error_body(
            "VALIDATION_ERROR", "请求参数校验失败", exc.errors()))

    @app.exception_handler(DesensitizeBlocked)
    async def _desens_handler(request: Request,
                              exc: DesensitizeBlocked) -> JSONResponse:
        """NFR-7：命中未脱敏敏感内容 → 403 + DESENSITIZE_BLOCKED。"""
        return JSONResponse(status_code=403, content=_error_body(
            "DESENSITIZE_BLOCKED", str(exc), exc.hits))

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        code = getattr(exc, "error_code", exc.__class__.__name__)
        detail = getattr(exc, "violations", None) or str(exc)
        # 分级：异常自带 `http_status` 时按它落状态码（如 LLMBudgetExceeded → 499），
        # 否则仍为 500。鸭子类型取值，**不 import agents**。
        status_code = int(getattr(exc, "http_status", 500))
        return JSONResponse(status_code=status_code, content=_error_body(
            code, str(exc), detail))

    return app


app = create_app()  # uvicorn mmagent.app:app 可直接使用
