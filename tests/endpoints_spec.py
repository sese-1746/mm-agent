"""`/api/v1` 端点的**唯一清单**（数据模块，不是测试文件，本身不收集任何用例）。

## 为什么单独抽成一个模块

`tests/test_frontend_modules.py` 与 `tests/test_api_frontend_routes.py` 都要用这份清单：

- 前者断言「每个端点在前端都有调用点」（静态扫 `frontend/assets/**` 的字面量）；
- 后者断言「这份清单 == 运行时真路由」（`create_app().routes`）。

以前两处各写一份、且**手抄的清单漏了 8 条也没人发现**——`test_8b` 只断言
`len(ENDPOINTS) == 16`，把漏项锁成了「正确」。这是本项目第 4 次「测试守护缺陷」。
抽成单一来源后，只要有一条路由没进清单，集合等式立刻变红。

## 维护规则（唯一一条）

**清单必须与运行时一起长，任何时刻都不许有差。**
改法是：先加/删路由，再改这里，最后跑
`tests/test_api_frontend_routes.py::test_路由清单与运行时集合相等`。
不许为了让测试变绿而提前把还没实现的路由写进来（那会让「清单 36 / 运行时 30」这种
说不清的红灯出现，也等于把清单变成愿望单）。

## 路径写法

`path` 一律用 FastAPI 的占位写法 `{name}`，**不带** `/api/v1` 前缀
（与 `create_app().routes` 去掉前缀后的形状一致，便于直接比对）。
"""

from __future__ import annotations

# (method, path)；method 只用于人读与文档，比对主体是 path 集合。
ENDPOINTS: list[tuple[str, str]] = [
    # ---- 基础 ----
    ("GET", "/health"),
    # ---- 体检 / 求解 / 出图 ----
    ("POST", "/intake/run"),
    ("POST", "/solve/run"),
    ("POST", "/figures/render"),
    # ---- 档案（FR-9 / FR-1） ----
    ("GET", "/profile/show"),            # 兼容别名：等价 GET /profile/{profile_id}?version=
    ("POST", "/profile/write"),
    ("GET", "/profile/{profile_id}"),
    ("GET", "/profile/{profile_id}/versions"),
    # ---- 人机回环 HITL ----
    ("POST", "/run/start"),
    ("POST", "/run/resume"),
    ("GET", "/run/status"),
    # ---- 实时推流（SSE，POST + body，故不能用 EventSource） ----
    ("POST", "/stream/run"),
    # ---- 合规 / 安全 / 审计 ----
    ("POST", "/compliance/disclosure"),
    ("GET", "/env/attest"),
    ("GET", "/netguard/check"),
    ("POST", "/desensitize/check"),
    ("GET", "/audit/net"),
    ("GET", "/audit/events"),
    # ---- 产物台账 ----
    ("GET", "/artifacts/list"),
    # ---- 历年真题与官方论文素材库（FR-14） ----
    ("GET", "/archive/list"),
    ("GET", "/archive/summary"),
    ("GET", "/archive/item/{archive_id}"),
    ("GET", "/archive/page/{archive_id}/{page_no}"),
    ("GET", "/archive/file/{archive_id}"),
    # ---- 产品级能力薄封装（第 31 轮）：原本只能 CLI 跑的能力补 HTTP 入口 ----
    ("POST", "/problem/parse"),
    ("POST", "/plan/build"),
    ("POST", "/paper/build"),
    ("GET", "/progress/status"),
    ("POST", "/consistency/check"),
    ("GET", "/profile/{profile_id}/trace"),
    # ---- 第 35 轮：上传（base64 JSON）+ 「一句话 → 自动 spec → 生成并运行」 ----
    ("POST", "/upload"),
    ("POST", "/solve/generate"),
    # ---- 第 36 轮：RAG 向量检索（索引落 artifacts/rag/index/，不建表） ----
    ("POST", "/rag/search"),
    # 数模论文工作台：Word 文献 → 简易综述 + Word 产物
    ("POST", "/review/generate"),
    ("POST", "/evaluation/compare"),
]

# 当前条数（供测试打印，不作为断言真值；真值由「清单 == 运行时」的集合等式保证）。
ENDPOINT_COUNT = len(ENDPOINTS)
