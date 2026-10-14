"""FastAPI 应用工厂：挂载 4 条路由 + 统一错误体。

错误体固定结构（所有异常处理器一致）：
{"error_code": str, "message": str, "detail": any, "trace_id": str}
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import (routes_compliance, routes_intake, routes_profile,
                  routes_solve)
from .security.desensitize import DesensitizeBlocked

API_PREFIX = "/api/v1"


def _error_body(error_code: str, message: str, detail=None,
                trace_id: str | None = None) -> dict:
    """构造统一错误体。"""
    return {"error_code": error_code, "message": message, "detail": detail,
            "trace_id": trace_id or uuid.uuid4().hex}


def create_app() -> FastAPI:
    """创建并返回 FastAPI 应用。"""
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
        return JSONResponse(status_code=500, content=_error_body(
            code, str(exc), detail))

    return app


app = create_app()  # uvicorn mmagent.app:app 可直接使用
