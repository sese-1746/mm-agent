"""环境自证与守卫路由（NFR-7 / NFR-8）。

## `api → cli` 依赖（README §3.7 **方案甲**）

本模块直接 `from ..cli import ...` 复用 `cli` 的自证与名单制实现
（`env_attest_report` / `load_net_allowlist` / `netguard_scan`）；词法上属
**反向依赖**（`api` 层 import 顶层入口模块 `cli`）。已在 `docs/IMPL_DEVIATIONS.md`
登记（含后续项 **A9**）。选择理由见实现报告：方案乙（整体搬迁 `cli.py` 自证核心）
改动面大、回归风险高，且须同步改 `tests/test_netguard_allowlist.py` 的自查扫描目标。

## 口径

- `env/attest`：键与 `cli env-attest` 落盘 JSON **同源**（7 键）。
- `netguard/check`：`exit_code = 1 if (violations + test_violations) else 0`（与 CLI 一致）。
- `desensitize/check`：`persist` 默认 **true**（命中即留痕）；`return_masked` 默认 **false**
  （避免掩码文本无谓外流）。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..cli import env_attest_report, load_net_allowlist, netguard_scan
from ..security.desensitize import record_hits, scan_and_mask
from ..store.db import DEFAULT_DB_PATH

router = APIRouter(prefix="/api/v1", tags=["ops"])

# 命中留痕落库路径（模块级便于测试隔离；默认与 CLI 一致）。
DESENSITIZE_DB = DEFAULT_DB_PATH


class DesensitizeRequest(BaseModel):
    """脱敏检查请求体。"""
    text: str
    field_raw: str = ""
    table_id: str = ""
    persist: bool = True
    return_masked: bool = False


@router.get("/env/attest")
def env_attest(out: str = "") -> dict:
    """环境自证报告（7 键）；`out` 给了则同时落盘 JSON。"""
    attest = env_attest_report()
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json

        path.write_text(json.dumps(attest, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return attest


@router.get("/netguard/check")
def netguard_check() -> dict:
    """名单制出网检查（口径与 `cli netguard-check` 一致）。"""
    allowlist = load_net_allowlist()
    report = netguard_scan(allowlist, exclude=set())
    exit_code = 1 if (report["violations"] + report["test_violations"]) else 0
    return {"ok": exit_code == 0, "exit_code": exit_code,
            "allowlist": allowlist, "report": report}


@router.post("/desensitize/check")
def desensitize_check(req: DesensitizeRequest) -> dict:
    """逐行脱敏检查；`ok` 表示**无命中**（与 CLI 退出码口径一致）。"""
    lines = req.text.splitlines()
    masked, hits = scan_and_mask(lines, field_raw=req.field_raw,
                                 table_id=req.table_id)
    if req.persist and hits:
        record_hits(hits, db_path=DESENSITIZE_DB)
    return {
        "ok": len(hits) == 0,
        "lines": len(lines),
        "hit_count": len(hits),
        "hits": hits,
        "masked_text": "\n".join(masked) if req.return_masked else None,
    }
