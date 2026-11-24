"""命令行入口：python -m mmagent.cli <子命令>。

子命令：
- --version                        打印版本（`mmagent 0.1.0`，规划书 §9.5）
- db init --db data\\mm.db          建库（schema.sql）
- intake --dataset <dir> --out <dir> 附件体检
- env-attest --out <path>          环境留存（python/依赖版本 + Ollama 模型列表）
- env-attest --verify <path>       环境校验（只判 4 项，规划书 §5.4）
- net-audit [--since <ts>] [--out <path>]  出网台账检索（规划书 §12.3）
- ollama verify --models a,b,c     本地模型就绪校验（规划书 §12.1 步骤 3）
- report acceptance                仓库验收自证（5 项，规划书 §12.2）
- netguard-check                   名单制出网检查（白名单外 host 判违规）
- solve --spec <json> --workdir <dir>  FR-5 求解：生成→守卫→执行→修复（P0-2，exit 0/5）
- run start|resume|status          人机回环（LangGraph interrupt + SqliteSaver）
- serve                            启动本地 HTTP 服务（API + 静态前端；默认仅回环）
- re-plot                          FR-6 重出图（P0-3，结果表 + figure specs → PNG dpi300）
- profile show                     FR-9 读档案版本（P0-4，固定 8 键 JSON）
- problem parse                    FR-1 题面 → 结构化子问题（三要素 + 原文引用），写 draft 档案
- plan build                        FR-3 题面 → ≥3 个候选方法论 + 反造假，落 artifacts/plan/
- paper build                       FR-7 档案 → 五章 docx 骨架 + sidecar manifest（**不调 LLM**）
- export build                      FR-12 paper.docx → 可交付目录（页脚署名 + 四道阻断闸 + SHA256SUMS）
- compliance build                 FR-13 生成 AI 使用披露（P0-5，md + docx）
- progress status                    FR-8 倒计时 + 无产出预警 + 不做清单（只读不写库）
- consistency check                  FR-10 正文数字 vs 结果文件（只读，exit 4=有数字无出处）
- archive ingest|list|summary        FR-14 历年真题 / 官方论文档案库（exit 4=素材缺失或哈希不符）
- rag rebuild|search                FR-4 向量索引重建 / 检索（索引落 artifacts/rag/index/，不建表）
- spotcheck [--case ID]            只读比对当前树 vs NFR-4 冻结基线（PASS=0 / FAIL=1 / 配置错=2）
- freeze-golden [--write]          重跑产线并与冻结基线比对（**默认 dry-run 不写盘**）
- regression run|stats             NFR-4 长期回归台账（N<10 只报 k/N，不报比率）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from importlib import metadata
from pathlib import Path

import yaml

from . import __version__
from .metrics import (HASH_CALIBER, compare_sums, dump_sums, load_sums, scan_files,
                      summarize_runs)

# ---------------------------------------------------------------- 常量

REPO_ROOT = Path(__file__).resolve().parent.parent

# NFR-4 冻结基线（B1）：真值 = 本产线确定性产物的 sha256（README task-003 §3.1，D-15）
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
GOLDEN_CASES = GOLDEN_DIR / "cases.json"
GOLDEN_SUMS = GOLDEN_DIR / "SHA256SUMS"
REGRESSION_LEDGER = REPO_ROOT / "artifacts" / "reports" / "regression" / "ledger.jsonl"

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
TRACKED_DEPS = ["fastapi", "uvicorn", "pandas", "openpyxl", "xlrd", "matplotlib",
                "python-docx", "pydantic", "PyYAML", "httpx", "Jinja2", "pytest",
                # task-002 人机回环：本地 LangGraph 版本须可追溯（零外网）
                "langgraph", "langgraph-checkpoint-sqlite"]

# ------------------------------------------------------------------ 名单制出网
# 口径（2026-10-16）：规划书 §3.3「禁云」修订为「**名单制出网**」——出网仅限白名单 host
# （回环 + DashScope 域名），其余一律禁止；规划书 §12.3「抓包外发=0」改为「**白名单核验**」。
# 说明：`pipeline/guard.py` 的 import 白名单是给 **LLM 生成的脚本** 用的，**不适用于产品代码**；
# 产品代码里 `import os` / `import httpx` 合法，但出网目标必须落在白名单内。

# 可发 HTTP/S 请求的客户端库：其出现的**绝对 URL host** 必须落在白名单内（不强禁 import）
NET_CLIENT_MODULES = ("httpx", "aiohttp", "requests", "urllib3")
# 原始/低层网络原语：产品代码一律禁止 import（无法用 URL 字面量约束目标）
RAW_NET_MODULES = ("socket", "ftplib", "http", "urllib.request", "urllib.error")
# 视为「出网点」的调用：模块级 get/post/... 与客户端构造
_HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options",
                 "request", "stream", "Client", "AsyncClient")
# 兜底白名单（models.yaml 缺 net_allowlist 时使用）
DEFAULT_NET_ALLOWLIST = ("127.0.0.1", "localhost", "dashscope.aliyuncs.com")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://([^/?#\s]+)")

# 「客户端出网目标必须来自配置」检查（A0-1，**A0-10 升级为白名单式**，A0-13 补直名别名）：
# `httpx.Client(base_url=<配置>)` 是配置驱动、静态不可解析，故「白名单闭环」真正的护栏是
# `_config_host_violations`。缺口在于：若有人把 base_url 改成读环境变量 / 命令行参数 /
# 形参直传 / 间接算出的字符串，闭环失效而闸门仍显示通过。
#
# **升级理由（A0-10）**：原先按「黑名单句法」判定（只拦实参节点直接是 `os.environ*`/`getenv`/
# `sys.argv`/形参），**任何间接形态**都能绕过 —— `A + B` 拼接、先赋值给变量再传（跨语句）、
# `**kwargs` 展开、f-string、函数内局部变量，实测 **5/5 全部逃脱**。黑名单永远不完备。
# 改为**白名单式**：只放行**句法层可静态确认为「配置来源」**的形态，其余一律判违规：
#   允许：`Call`（如 `load_models_config()`）/ `Subscript`（配置字典下标）/ `Attribute`
#         （如 `self.base_url`）/ 字符串字面量（其 host 须在白名单内）/
#         裸 `Name` 且能**一跳解析**到「模块级 `NAME = "<字面量>"`」（host 须在白名单内）；
#   禁止：裸 `Name`（解析不到）、`BinOp`、`JoinedStr`(f-string)、`IfExp`、以及调用里的 `**` 展开；
#   显式来源（落在允许形态里也拦）：`os.environ*` / `os.getenv` / `sys.argv`。
# **覆盖面边界（A0-13 后改写的准确表述，两句）**：
#   ① 覆盖**同一模块内**的调用点 —— 含 `模块.方法(...)`（`Attribute`）与
#      `from 模块 import 方法 [as 别名]` 后的**直名调用**（`Name`）两种形态，同一套白名单规则；
#   ② **不做**跨函数 / 跨模块的数据流分析（间接来源仍需运行期 `_config_host_violations` 闭环 + 评审兜底）。
# **同层仍判不到的形态（A0-16；属已知未覆盖面，非缺陷）**：
#     ① 属性赋值流 —— `s = Session(); s.base_url = os.environ["X"]`（URL 不在调用点，而在**属性赋值**上）；
#     ② 动态取属性 —— `getattr(httpx, "Client")(base_url=os.environ["X"])`（调用目标名非字面量）。
#   两者均**不判违规**，落在上面两句边界之外；本检查**不做属性流 / 动态调用分析**。
_URL_TARGET_KWARGS = ("base_url", "url")
_FORBIDDEN_URL_SOURCE_PREFIXES = ("os.environ", "os.getenv", "sys.argv")
# 模块级 URL 方法 → 第几个位置参数是「出网目标」（`request(method, url)` → 1，其余 → 0）
_POSITIONAL_URL_INDEX = {"request": 1, "get": 0, "post": 0, "put": 0, "delete": 0,
                         "patch": 0, "head": 0, "options": 0, "stream": 0}
# 「原始网络原语 import」违规行的统一标记（用于 A0-8 的豁免判定，避免按文案分支匹配）
RAW_IMPORT_MARK = "违规 import 原始网络原语"


# ---------------------------------------------------------------- 工具函数

def load_net_allowlist(config_path: Path | None = None) -> list[str]:
    """从 `config/models.yaml` 读 `net_allowlist`（缺失则用 DEFAULT_NET_ALLOWLIST）。"""
    path = config_path or (REPO_ROOT / "config" / "models.yaml")
    try:
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        hosts = [str(h).strip().lower() for h in (data.get("net_allowlist") or []) if str(h).strip()]
        return hosts or list(DEFAULT_NET_ALLOWLIST)
    except Exception:  # noqa: BLE001  配置不可读 → 退化到最小白名单
        return list(DEFAULT_NET_ALLOWLIST)


def host_allowed(host: str, allowlist: list[str]) -> bool:
    """host 是否在白名单内（精确匹配，或为其子域；大小写不敏感）。"""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    for entry in allowlist:
        e = entry.strip().lower().lstrip("*.").rstrip(".")
        if not e:
            continue
        if h == e or (e not in ("127.0.0.1", "::1") and h.endswith("." + e)):
            return True
    return False


def _host_of_url(url: str) -> str:
    """从 URL 字面量取 host（去 userinfo 与端口）；非绝对 URL 返回 ''。"""
    m = _URL_RE.match(url.strip())
    if not m:
        return ""
    authority = m.group(1)
    if "@" in authority:
        authority = authority.rsplit("@", 1)[-1]
    if authority.startswith("["):            # IPv6 字面量 [::1]:8080
        return authority[1:authority.find("]")] if "]" in authority else authority
    return authority.rsplit(":", 1)[0] if ":" in authority else authority


def _urlish_strings(tree) -> list[tuple[str, int]]:
    """收集文件内所有「绝对 URL 形态」的字符串常量（含 f-string 的常量片段）→ [(url, lineno)]。"""
    import ast

    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _host_of_url(node.value):
                out.append((node.value, getattr(node, "lineno", 0)))
        elif isinstance(node, ast.JoinedStr):   # f"https://host/{x}"
            joined = "".join(v.value for v in node.values
                             if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if _host_of_url(joined):
                out.append((joined, getattr(node, "lineno", 0)))
    return out


def _dotted(node) -> str:
    """把 `Name` / `Attribute` 链渲染成点分名（`os.environ.get` → "os.environ.get"）；否则 ""。"""
    import ast

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _function_param_names(tree) -> set[str]:
    """收集文件内所有函数（含 async / *args / **kwargs）的形参名。"""
    import ast

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = node.args
        for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            names.add(arg.arg)
        if a.vararg is not None:
            names.add(a.vararg.arg)
        if a.kwarg is not None:
            names.add(a.kwarg.arg)
    return names


def _module_str_consts(tree) -> dict[str, str]:
    """A0-10：收集**模块顶层** `NAME = "<字面量>"` / `NAME: T = "<字面量>"` → {名: 字面量}。

    供「一跳静态解析」用：模块级常量（如 `OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"`）
    作为裸 `Name` 出现在出网目标位时，可判合规（前提 host 在白名单内）。
    """
    import ast

    out: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        targets: list = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = value.value
    return out


def _is_forbidden_url_source(dotted: str) -> bool:
    """dotted 是否命中显式禁止来源前缀（`os.environ*` / `os.getenv` / `sys.argv*`）。"""
    return any(dotted == bad or dotted.startswith(bad + ".")
               for bad in _FORBIDDEN_URL_SOURCE_PREFIXES)


def _url_source_violation(node, allowlist: list[str], module_consts: dict[str, str],
                          param_names: set[str]) -> str | None:
    """A0-10 **白名单式**：返回出网目标的**违规原因**，合规则 `None`。

    只放行句法层可静态确认的配置来源（`Call` / `Subscript` / `Attribute` / 白名单内字符串字面量 /
    可一跳解析的模块级字符串常量裸 `Name`）；其余形态（`BinOp` / `JoinedStr` / `IfExp` / 解析不到的
    `Name` …）一律违规。落在允许形态里的显式来源（`os.environ*`/`os.getenv`/`sys.argv`）照样拦下。
    """
    import ast

    if isinstance(node, ast.Name):
        if node.id in param_names:
            return f"{node.id}（函数入参直传，非配置来源）"
        if node.id in module_consts:
            host = _host_of_url(module_consts[node.id])
            if host and host_allowed(host, allowlist):
                return None
            return f"{node.id}（模块级常量 host={host!r} 不在白名单 / 非绝对 URL）"
        return f"{node.id}（裸变量，静态无法确认来自配置）"

    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            host = _host_of_url(node.value)
            if host and host_allowed(host, allowlist):
                return None
            return f"字符串字面量 host={host!r}（不在白名单 / 非绝对 URL）"
        return f"非字符串字面量（{type(node.value).__name__}）"

    if isinstance(node, ast.Subscript):        # 配置字典下标；但 os.environ[...] / sys.argv[...] 禁止
        dotted = _dotted(node.value)
        return (f"{dotted}[…]（非配置来源：环境变量/命令行）"
                if _is_forbidden_url_source(dotted) else None)

    if isinstance(node, ast.Call):             # load_*_config() 等；但 os.getenv(...) / os.environ.get(...) 禁止
        dotted = _dotted(node.func)
        return (f"{dotted}(…)（非配置来源：环境变量/命令行）"
                if _is_forbidden_url_source(dotted) else None)

    if isinstance(node, ast.Attribute):        # self.base_url / cfg.base_url
        dotted = _dotted(node)
        return f"{dotted}（非配置来源）" if _is_forbidden_url_source(dotted) else None

    return f"{type(node).__name__} 表达式（静态不可确认来自配置）"


def _client_call_sites(tree, aliases: dict[str, str]):
    """A0-13：列出 HTTP 客户端调用点 —— 两种**同一句法层**的形态都覆盖：

    ① `httpx.Client(...)`（`func` 是 `Attribute`；`import httpx [as h]` 后 `h.Client(...)` 亦同）；
    ② `from httpx import Client [as C]` 后的**直名** `C(...)` / `get(...)`（`func` 是 `Name`）。

    产出 `(显示名, 模块名, 方法名, 调用节点)`。**同一套白名单规则**作用于两种形态；
    非 HTTP 客户端（如 `from pathlib import Path`）因模块根不在 `NET_CLIENT_MODULES` 而被过滤。
    """
    import ast

    direct: dict[str, tuple[str, str]] = {}      # 本地名 → (模块名, 原符号)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        mod = node.module or ""
        if mod.split(".")[0] not in NET_CLIENT_MODULES:
            continue
        for a in node.names:
            direct[a.asname or a.name] = (mod, a.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = func.value
            yield f"{base.id}.{func.attr}", aliases.get(base.id, base.id), func.attr, node
        elif isinstance(func, ast.Name) and func.id in direct:
            mod, sym = direct[func.id]
            yield func.id, mod, sym, node


def _client_target_violations(tree, path, param_names: set[str],
                              aliases: dict[str, str], allowlist: list[str]) -> list[str]:
    """A0-10/A0-13：HTTP 客户端的**出网目标**必须来自白名单内的配置来源。

    覆盖 `模块.方法(...)` 与 `from 模块 import 方法 [as 别名]` 的直名调用（同一套规则）。
    目标 = `base_url`/`url` 关键字实参 + 模块级 URL 方法的对应位置参数（`get/post/…` 第 1 个、
    `request` 第 2 个）。调用里出现 `**` 展开（无法核对实参名）→ 直接判违规。
    """
    module_consts = _module_str_consts(tree)
    out: list[str] = []
    for target, module, method, node in _client_call_sites(tree, aliases):
        if module.split(".")[0] not in NET_CLIENT_MODULES or method not in _HTTP_METHODS:
            continue
        for kw in node.keywords:               # ① `**kwargs` 展开 → 实参名检查失效，一律违规
            if kw.arg is None:
                out.append(f"{path}:{node.lineno}: HTTP 客户端 {target} 使用 `**` 展开实参"
                           f"（{_dotted(kw.value) or '…'}），无法核对出网目标 → 判违规")
        for kw in node.keywords:               # ② 具名 base_url / url
            if kw.arg not in _URL_TARGET_KWARGS:
                continue
            why = _url_source_violation(kw.value, allowlist, module_consts, param_names)
            if why:
                out.append(f"{path}:{node.lineno}: HTTP 客户端 {target} 的 {kw.arg} "
                           f"来源不合规：{why}（必须来自配置读取）")
        idx = _POSITIONAL_URL_INDEX.get(method)           # ③ 位置参数（模块级 URL 方法）
        if idx is not None and len(node.args) > idx:
            why = _url_source_violation(node.args[idx], allowlist, module_consts, param_names)
            if why:
                out.append(f"{path}:{node.lineno}: HTTP 客户端 {target} 的第 {idx + 1} 个位置参数 "
                           f"来源不合规：{why}（必须来自配置读取）")
    return out


def _is_test_file(path: Path) -> bool:
    """A0-4：文件是否位于 `tests/` 下（分级扫描：测试只查原语禁令 + 信息性列示）。"""
    return "tests" in Path(path).parts


def check_netguard_file(path: Path, allowlist: list[str], *,
                        product_code: bool = True) -> dict:
    """名单制检查单个文件 → `{"violations": [...], "outbound": [...], "informational": [...]}`。

    - 违规（**任何层级**）：import 原始网络原语（`socket`/`ftplib`/`http`/`urllib.request`）。
    - 违规（**仅产品代码**，`product_code=True`）：
      ① 任一绝对 URL 字面量的 host 不在白名单；
      ② A0-10/A0-13：HTTP 客户端（`httpx.Client(...)` 与 `from httpx import Client [as C]` 后的
         直名 `C(...)`，同一套规则）的**出网目标**必须是白名单内的配置来源。
    - 出网检点（信息）：
      `outbound` = 产品代码的 HTTP 客户端构造/模块级调用；
      `informational` = 测试目录内同样内容 + 测试里的 URL 字面量（**不判违规**，避免反例假阳性）。
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return {"violations": [f"{path}: 解析失败（{exc.msg}），未做联网检查"],
                "outbound": [], "informational": []}

    aliases: dict[str, str] = {}          # 本地名 → 模块名（import httpx as h / from httpx import Client）
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                aliases[(a.asname or root)] = a.name
                if a.name in RAW_NET_MODULES or root in ("socket", "ftplib", "http"):
                    violations.append(
                        f"{path}:{node.lineno}: {RAW_IMPORT_MARK} {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if mod in RAW_NET_MODULES or root in ("socket", "ftplib", "http"):
                violations.append(
                    f"{path}:{node.lineno}: {RAW_IMPORT_MARK}（from {mod} import …）")
            for a in node.names:
                aliases[(a.asname or a.name)] = mod

    informational: list[str] = []
    # ① 绝对 URL 字面量 → host 必须在白名单（产品代码判违规；测试仅信息性列示）
    for url, lineno in _urlish_strings(tree):
        host = _host_of_url(url)
        if not host:
            continue
        if product_code:
            if not host_allowed(host, allowlist):
                violations.append(
                    f"{path}:{lineno}: 出网 host 不在白名单：{host!r}（url={url[:80]!r}）")
        else:
            informational.append(
                f"{path}:{lineno}: [测试内 URL 字面量·信息性] host={host!r}（url={url[:80]!r}）")

    # ② A0-1/A0-10：客户端目标来源必须是白名单内的配置（仅产品代码）
    if product_code:
        violations.extend(_client_target_violations(
            tree, path, _function_param_names(tree), aliases, allowlist))

    # ③ 出网点（信息）—— 与 ② 同一份调用点清单，避免直名别名形态成为检出盲区（A0-13）
    outbound: list[str] = []
    for target, module, method, node in _client_call_sites(tree, aliases):
        if module.split(".")[0] not in NET_CLIENT_MODULES or method not in _HTTP_METHODS:
            continue
        literal = ""
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal = arg.value
                break
        host = _host_of_url(literal) if literal else ""
        detail = f"host={host!r}" if host else "host=<配置驱动/未解析>"
        outbound.append(f"{path}:{node.lineno}: {target}(...) {detail}")
    return {"violations": violations, "outbound": outbound, "informational": informational}


def _exemption_scope_error(rel: str) -> str | None:
    """A0-9/A0-14：豁免**只对 `tests/**` 生效**。非测试路径 / 畸形路径 → 返回错误说明（不生效）。

    后门修复：豁免通道若对产品代码也生效，则登记一下 `mmagent/**` 就能让闸门看不见它
    （**自证清白**）。故把作用域**限定为 `tests/**` 下的 `.py` 文件**；越界登记直接判**非法登记**，
    不静默忽略（静默忽略 = 另一种静默失败）。含 `..` 段的路径（如 `tests/../mmagent/cli.py`）
    属**脏数据**（看着像合法其实指产品）→ 一并判非法登记。
    """
    parts = rel.split("/")
    if ".." in parts:
        return f"畸形豁免路径 {rel!r}（含 `..` 段，疑似指向产品代码）"
    if not parts or parts[0] != "tests":
        return (f"豁免仅对 `tests/**` 生效；登记的非测试路径 {rel!r} 不生效"
                f"（不得给产品代码开后门）")
    if len(parts) < 2 or not rel.endswith(".py"):
        return f"畸形豁免路径 {rel!r}（须是 `tests/**` 下的 .py 文件）"
    return None


def load_raw_import_exemptions(config_path: Path | None = None
                               ) -> tuple[dict[str, str], list[str]]:
    """读 `models.yaml:net_raw_import_exempt` → `({相对路径: 理由}, [配置错误…])`。

    条目形如 `{path: "tests/x.py", reason: "…"}`。三条硬要求（任一不满足 → 该条目不生效并报违规）：
    1. **作用域限 `tests/**`**（A0-9）：登记非测试路径（如 `mmagent/**`）→ 判**非法登记**，不生效；
    2. `path` 须是 `tests/**` 下的 `.py` 文件（畸形值如空串 / `"*"` / 目录 / 含 `..` 段 → 不生效）；
    3. **`reason` 非空**（强制「豁免必须写明理由」，不允许注释式/魔法注释绕过）。
    """
    path = config_path or (REPO_ROOT / "config" / "models.yaml")
    mapping: dict[str, str] = {}
    errors: list[str] = []
    try:
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return {}, [f"{path}: 配置不可读（{exc}），无法核对原语 import 豁免登记"]
    entries = data.get("net_raw_import_exempt") or []
    if not isinstance(entries, list):
        return {}, [f"{path}: net_raw_import_exempt 必须是列表"]
    for i, entry in enumerate(entries):
        item = entry if isinstance(entry, dict) else {}
        rel = str(item.get("path") or "").strip().lstrip("./").replace("\\", "/")
        reason = str(item.get("reason") or "").strip()
        if not rel or not reason:
            errors.append(f"{path}: net_raw_import_exempt[{i}] 缺 `path` 或 `reason`"
                          f"（豁免必须写明理由）→ 不生效")
            continue
        scope_err = _exemption_scope_error(rel)
        if scope_err:
            errors.append(f"{path}: net_raw_import_exempt[{i}] {scope_err} → 不生效")
            continue
        mapping[rel] = reason
    return mapping, errors


def _is_raw_import_violation(text: str) -> bool:
    """该违规行是否属于「原始网络原语 import 禁令」（A0-8 口径 ②，可被显式登记豁免）。"""
    return RAW_IMPORT_MARK in text


def _config_host_violations(allowlist: list[str],
                            config_path: Path | None = None) -> list[str]:
    """配置层核对：`config/models.yaml` 各 base_url 的 host 必须落在白名单内（名单制闭环）。"""
    path = config_path or (REPO_ROOT / "config" / "models.yaml")
    out: list[str] = []
    try:
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        return [f"{path}: 配置不可读（{exc}），无法核对出网白名单"]
    for section in ("ollama", "dashscope"):
        url = str(((data.get(section) or {}).get("base_url")) or "")
        host = _host_of_url(url)
        if host and not host_allowed(host, allowlist):
            out.append(f"{path}: {section}.base_url 的 host 不在白名单：{host!r}")
    return out


def _dep_versions() -> dict:
    """读取关键依赖的已装版本（未装记为 None）。"""
    out = {}
    for name in TRACKED_DEPS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _ollama_models() -> dict:
    """直连本地 Ollama 拉模型列表；连不上返回 available=False（不抛异常）。"""
    try:
        import httpx

        resp = httpx.get(OLLAMA_TAGS_URL, timeout=3.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return {"available": True, "base_url": OLLAMA_TAGS_URL, "models": models}
    except Exception as exc:
        return {"available": False, "base_url": OLLAMA_TAGS_URL, "error": str(exc)}


def _iter_repo_py(scan_root: Path, exclude: set[Path]) -> list[Path]:
    """遍历仓库 `*.py`（**含 `tests/`**，由调用方分级处理），排除环境/产物目录。

    排除项（保持不变）：
    - `.agent-runs/`：调查/测试/验证探针脚本（合法出现 `import socket` 等字样）；
    - `artifacts/`：运行产物（含 basetemp 夹具脚本，甚至有语法错误的反例）—— 排除它修的是
      既有缺陷 N2（历史 EXIT=1 全来自 `artifacts/tmp/bt_*/broken.py`），属**净改善**。

    **不再排除 `tests/`**（A0-4）：测试目录改为**分级扫描** —— 仍查「原始网络原语 import 禁令」，
    出网点只做**信息性列示**，仅**跳过 URL-host 违规规则**（测试含反例字面量，按 URL 判定必假阳性）。
    分级由 `_is_test_file()` 判定，见 `netguard_scan()`。
    """
    excluded_parts = {".venv", "venv", "__pycache__", ".git", ".agent-runs", "artifacts"}
    root = Path(scan_root)
    files = []
    for p in root.rglob("*.py"):
        try:
            rel_parts = p.relative_to(root).parts      # 按**相对 scan_root** 的路径判排除，
        except ValueError:                             # 使本函数可对任意根复用（如测试 tmp 根）
            rel_parts = p.parts
        if any(part in excluded_parts for part in rel_parts):
            continue
        if p.resolve() in exclude:
            continue
        files.append(p)
    return sorted(files)


def netguard_scan(allowlist: list[str], *, scan_root: Path = REPO_ROOT,
                  exclude: set[Path] | None = None,
                  exemptions: dict[str, str] | None = None) -> dict:
    """A0-4/A0-8：分级名单制扫描 → 产品/测试分开计数与分类的结果 dict。

    **口径三条（A0-8 落定）**：
    ① URL-host 违规规则 → **仅产品代码**，决定 EXIT；
    ② 原始网络原语 import 禁令 → **产品与测试一致生效**，测试命中记 `test_violations` 且**同样致 EXIT≠0**；
    ③ informational 出网点 → **不参与 EXIT**（仅列示）。

    豁免（`net_raw_import_exempt`）：文件级显式登记（相对 `scan_root`、正斜杠）+ **非空理由**，
    **且仅对 `tests/**` 生效**（A0-9：禁止给产品代码自证清白）；命中豁免的**测试文件**，其
    原语 import 违规转入 `exempted`（不参与 EXIT），其它规则照常生效；缺理由 / 越界（非 tests）
    的登记**不生效**且本身报为违规（不静默忽略）。

    返回键：
    - `violations`：**产品代码**违规（URL-host / 客户端目标来源 / 原语 import / `models.yaml` 配置层）；
    - `test_violations`：**测试目录内**的原语 import 违规；
    - `exempted`：已登记豁免而放行的原语 import 记录（附理由）；
    - `outbound`：产品代码出网点（信息，③）；
    - `informational`：测试目录内出网点 + URL 字面量（信息，③）；
    - `product_scanned` / `test_scanned`：两类文件计数（分开报，便于复核覆盖）。
    """
    exclude = exclude or set()
    root = Path(scan_root)
    if exemptions is None:
        exemptions, exempt_cfg_errors = load_raw_import_exemptions()
    else:
        exempt_cfg_errors = []
    violations: list[str] = []
    test_violations: list[str] = []
    exempted: list[str] = []
    outbound: list[str] = []
    informational: list[str] = []
    product_scanned = test_scanned = 0
    for path in _iter_repo_py(root, exclude):
        is_test = _is_test_file(path)
        report = check_netguard_file(path, allowlist, product_code=not is_test)
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = path.as_posix()
        reason = exemptions.get(rel)
        applies = bool(reason) and is_test          # A0-9：豁免**只对 tests/** 生效
        raw_hits = [v for v in report["violations"] if _is_raw_import_violation(v)]
        if applies:
            exempted.extend(f"{v}（已登记豁免：{reason}）" for v in raw_hits)
        hard = [v for v in report["violations"]
                if not (applies and _is_raw_import_violation(v))]
        if is_test:
            test_scanned += 1
            test_violations.extend(hard)
            informational.extend(report["outbound"])
            informational.extend(report["informational"])
        else:
            product_scanned += 1
            violations.extend(hard)
            outbound.extend(report["outbound"])
    violations.extend(_config_host_violations(allowlist))
    violations.extend(exempt_cfg_errors)
    return {"violations": violations, "test_violations": test_violations,
            "exempted": exempted, "outbound": outbound, "informational": informational,
            "product_scanned": product_scanned, "test_scanned": test_scanned}


def _net_violations(path: Path) -> list[str]:
    """兼容旧接口：按默认白名单对单文件做名单制检查，只返回违规项。"""
    return check_netguard_file(path, load_net_allowlist())["violations"]


# ---------------------------------------------------------------- 子命令

def cmd_db_init(args) -> int:
    """建库：执行 store/schema.sql。"""
    from .store.db import init_db

    path = init_db(args.db)
    print(f"[db init] 已初始化：{path}")
    return 0


def cmd_intake(args) -> int:
    """附件体检。"""
    from .pipeline.intake import run_intake_audit

    summary = run_intake_audit(args.dataset, args.out)
    print(f"[intake] 表数={len(summary['tables'])} 字段数={summary['field_count']} "
          f"冲突数={summary['conflict_count']} → {summary['out_dir']}")
    return 0


def env_attest_report() -> dict:
    """环境自证报告：python / 依赖版本 + Ollama 模型列表。

    **同源三处**：`cli env-attest` 落盘 JSON、`GET /api/v1/env/attest` 响应、
    本函数返回的 7 键 —— 键集合恒为
    `python_version / python_implementation / platform / executable /
    dependencies / requirements_sha256_16 / ollama`。
    """
    req_path = REPO_ROOT / "requirements.txt"
    req_hash = (hashlib.sha256(req_path.read_bytes()).hexdigest()[:16]
                if req_path.exists() else None)
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "dependencies": _dep_versions(),
        "requirements_sha256_16": req_hash,
        "ollama": _ollama_models(),
    }


def cmd_env_attest(args) -> int:
    """环境留存：python、依赖、Ollama 模型列表；或校验一份已有留存（`--verify`）。"""
    if getattr(args, "verify", None):
        return cmd_env_attest_verify(args.verify)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    attest = env_attest_report()
    out.write_text(json.dumps(attest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[env-attest] 已写入：{out}"
          f"（ollama available={attest['ollama']['available']}）")
    return 0


# `env-attest --verify` 的**判定键**（规划书 §5.4；裁决：就这 4 项）
ATTEST_VERIFY_KEYS = ("python_version", "python_implementation",
                      "dependencies", "requirements_sha256_16")
# 只打印为 INFO、**不参与判定**的键：
# - `executable`：venv 重建后路径必变，判它等于永远 FAIL；
# - `ollama`：模型列表是运行环境状态（可增删），不是代码依赖。
ATTEST_INFO_KEYS = ("platform", "executable", "ollama")


def _diff_attest(expected: dict, actual: dict) -> tuple[list[str], list[str]]:
    """逐项比对留存与当前环境，返回 `(缺键, 值不一致)` 两类问题（**必须分开报**）。

    缺键 → 无法判定（exit 2）；值不同 → 判 FAIL（exit 1）。
    `dependencies` 走**逐包比对**，缺包记 `dependencies.<pkg>` 缺键。
    """
    missing: list[str] = []
    diffs: list[str] = []
    for key in ATTEST_VERIFY_KEYS:
        if key not in expected:
            missing.append(key)
            continue
        if key == "dependencies":
            exp_dep, act_dep = expected.get(key), actual.get(key)
            if not isinstance(exp_dep, dict) or not isinstance(act_dep, dict):
                diffs.append(f"{key}: expected={exp_dep!r} actual={act_dep!r}")
                continue
            for pkg in sorted(set(exp_dep) | set(act_dep)):
                if pkg not in exp_dep:
                    missing.append(f"dependencies.{pkg}")
                elif pkg not in act_dep:
                    diffs.append(f"dependencies.{pkg}: "
                                 f"expected={exp_dep[pkg]!r} actual=<未安装>")
                elif exp_dep[pkg] != act_dep[pkg]:
                    diffs.append(f"dependencies.{pkg}: "
                                 f"expected={exp_dep[pkg]!r} actual={act_dep[pkg]!r}")
            continue
        if expected.get(key) != actual.get(key):
            diffs.append(f"{key}: expected={expected.get(key)!r} "
                         f"actual={actual.get(key)!r}")
    return missing, diffs


def cmd_env_attest_verify(path_arg: str) -> int:
    """`env-attest --verify <path>`：与当前环境比对 4 项。

    退出码：一致 0 / 有不一致 1 / 文件不存在·非法 JSON·缺必需键 2。
    """
    path = Path(path_arg)
    if not path.is_file():
        print(f"[env-attest] 文件不存在：{path}", file=sys.stderr)
        return 2
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[env-attest] 非法 JSON：{path}（{exc}）", file=sys.stderr)
        return 2
    if not isinstance(expected, dict):
        print("[env-attest] 非法留存：顶层必须是 JSON 对象", file=sys.stderr)
        return 2

    actual = env_attest_report()
    missing, diffs = _diff_attest(expected, actual)

    print(f"[env-attest] 比对基准：{path}")
    print(f"[env-attest] 判定项 {len(ATTEST_VERIFY_KEYS)}："
          f"{' / '.join(ATTEST_VERIFY_KEYS)}")
    for key in ATTEST_INFO_KEYS:            # 仅 INFO，不参与判定
        print(f"[env-attest][INFO] {key} = {expected.get(key)!r}")

    if missing:
        for key in missing:
            print(f"missing key: {key}", file=sys.stderr)
        print(f"[env-attest] 缺键 {len(missing)} 项 → 无法判定（exit 2）", file=sys.stderr)
        return 2
    if diffs:
        for line in diffs:
            print(f"[env-attest] {line}")
        print(f"[env-attest] 不一致 {len(diffs)} 项 → FAIL（exit 1）", file=sys.stderr)
        return 1
    print(f"[env-attest] PASS：{len(ATTEST_VERIFY_KEYS)} 项一致")
    return 0


def cmd_netguard_check(args) -> int:
    """名单制静态检查：白名单外 host 判违规，并检出 HTTP 客户端出网点（产品/测试分级）。

    口径（规划书 §3.3「禁云」→「名单制出网」；§12.3「抓包外发=0」→「白名单核验」）。
    **A0-8 正式口径三条（2026-10-16 task-002）**：
    1. **URL-host 违规规则 → 仅产品代码**（`mmagent/**` 的绝对 URL 字面量 host 必须在
       `models.yaml:net_allowlist` 内）→ **决定 `EXIT`**；
    2. **原始网络原语 import 禁令 → 产品与测试一致生效**（`socket`/`ftplib`/`http`/
       `urllib.request|error`）：测试命中记入 `test_violations`，**同样致 `EXIT≠0`**；
    3. **informational 出网点 → 不参与 `EXIT`**（仅列示：产品出网点 + 测试内出网点/URL 字面量）。

    另（A0-1 → **A0-10 升级为白名单式**）：产品代码里 HTTP 客户端的**出网目标**（`base_url`/`url`
    关键字实参，以及 `get/post/request(…)` 等的对应位置参数）**只放行句法层可静态确认为「配置来源」
    的形态**——`Call`（如 `load_*_config()`）/ `Subscript`（配置字典下标）/ `Attribute`（如
    `self.base_url`）/ 白名单内的字符串字面量 / 可**一跳解析**的模块级字符串常量裸 `Name`；其余
    （裸 `Name`、`A + B` 拼接、f-string、`IfExp`、调用里的 `**` 展开…）**一律判违规**。
    **覆盖面边界（A0-13 后改写）**：① 覆盖**同一模块内**的调用点 —— `模块.方法(...)`（`Attribute`）
    与 `from 模块 import 方法 [as 别名]` 后的**直名调用**（`Name`）两种形态，**同一套白名单规则**；
    ② **不做**跨函数 / 跨模块的数据流分析（间接来源仍需运行期 `_config_host_violations` 闭环 + 评审兜底）。
    **同层仍判不到的形态（A0-16；属已知未覆盖面，非缺陷）**：① **属性赋值流** ——
    `s = Session(); s.base_url = os.environ["X"]`（URL 不在调用点，而在**属性赋值**上）；
    ② **动态取属性** —— `getattr(httpx, "Client")(base_url=os.environ["X"])`（调用目标名非字面量）。
    两者均**不判违规**，落在上句边界之外；本检查**不做属性流 / 动态调用分析**。

    豁免（A0-8 规定 + A0-9 作用域收敛）：确有需要的**原语 import** 只能在
    `models.yaml:net_raw_import_exempt` 里**显式登记**，且**只对 `tests/**` 生效**（登记产品/非测试
    路径 → 判非法登记、不生效）；须 `path` + **非空 `reason`**，不允许注释式/魔法注释绕过。
    未登记、没写理由、或越界的一律判违规。
    排除项：`artifacts/`（修既有缺陷 N2，净改善）、`.agent-runs/`；`tests/` **不排除**（改分级扫描）。
    """
    allowlist = load_net_allowlist()
    # 不再排除扫描器自身：AST 检查不会被本文件里的字符串常量（如模块名清单）误伤，
    # 保留排除会形成盲区（在 cli.py 里加真实出网调用将不可见）。
    report = netguard_scan(allowlist, exclude=set())

    print(f"[netguard-check] 名单制：白名单={allowlist}")
    print(f"[netguard-check] 扫描：产品代码 {report['product_scanned']} 个文件 / "
          f"测试 {report['test_scanned']} 个文件（tests/ 分级：仅原语禁令判违规）")
    print(f"[netguard-check] 产品代码出网点 {len(report['outbound'])} 处"
          "（HTTP 客户端/构造，信息）：")
    for o in report["outbound"]:
        print("  " + o)
    if report["informational"]:
        print(f"[netguard-check] 测试内信息性出网点 {len(report['informational'])} 处"
              "（**不计违规**）：")
        for o in report["informational"]:
            print("  " + o)
    if report["exempted"]:
        print(f"[netguard-check] 已登记豁免的原语 import {len(report['exempted'])} 处"
              "（`net_raw_import_exempt`，**不计违规**）：")
        for e in report["exempted"]:
            print("  " + e)
    hard = report["violations"] + report["test_violations"]
    if hard:
        if report["violations"]:
            print("[netguard-check] 产品代码违规：")
            for v in report["violations"]:
                print("  " + v)
        if report["test_violations"]:
            print("[netguard-check] 测试内违规（原语 import 禁令）：")
            for v in report["test_violations"]:
                print("  " + v)
        return 1
    print("[netguard-check] 通过：无白名单外 host 出网、无原始网络原语 import、"
          "客户端目标均来自配置")
    return 0


def cmd_desensitize_check(args) -> int:
    """NFR-7 脱敏检查：逐行扫描输入文件，命中即报告并把记录写入 desensitize_hit。"""
    from .security.desensitize import record_hits, scan_and_mask

    src = Path(args.input)
    if not src.exists():
        print(f"[desensitize-check] 输入文件不存在：{src}")
        return 2
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    _, hits = scan_and_mask(lines, field_raw=args.field or "", table_id=args.table or "")

    masked_lines, _ = scan_and_mask(lines, field_raw=args.field or "",
                                    table_id=args.table or "")
    if args.mask_out:
        out = Path(args.mask_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(masked_lines) + "\n", encoding="utf-8")
        print(f"[desensitize-check] 脱敏副本已写入：{out}")

    if hits:
        record_hits(hits, db_path=args.db)
        print(f"[desensitize-check] 命中 {len(hits)} 处（已记入 desensitize_hit），明细：")
        for h in hits:
            print(f"  - rule={h['rule']} field={h['field_raw'] or '<无列名>'} "
                  f"action={h['action']} sha={h['raw_sha256']}")
        return 1
    print(f"[desensitize-check] 通过：{len(lines)} 行未命中敏感模式")
    return 0


# ---------------------------------------------------------------- 人机回环（run）

def _events_table_present(db_path: str) -> bool:
    """检测台账库是否已初始化（存在 events 表）。

    不自动建库——避免静默产生新文件（那正是本项目要消灭的"安静地丢数据"）。
    """
    import sqlite3

    path = Path(db_path)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _require_ledger(db_path: str) -> int | None:
    """台账库未初始化时打印明确错误并返回 exit code 3；就绪返回 None。"""
    if _events_table_present(db_path):
        return None
    print(
        f"[run] 台账库 {db_path} 缺少 events 表（尚未初始化），事件留痕将无法落库。\n"
        f"      请先执行：python -m mmagent.cli db init --db {db_path}",
        file=sys.stderr)
    return 3


# `--conflicts` / `--sanity-blocking` 是**模拟开关**（真实待审材料应由上层注入）；
# 未显式提供明细时，用与项目已知数据问题（README §9）一致的示例材料，便于人工看到"该审什么"。
_DEFAULT_CONFLICTS_REVIEW = [
    {"field": "评级",
     "reason": "同构外推列口径冲突（示例，见 README §9 已知数据问题 #3）",
     "candidates": ["qual_1_2_7_8_10", "def_7_8"]},
]
_DEFAULT_SANITY_REVIEW = [
    {"check": "unit_mismatch",
     "detail": "示例：单位/量纲不一致，需人工裁决后继续"},
]


def _parse_review_json(text: str, label: str):
    """解析可选的待审材料 JSON；非法时打印错误并返回 (None, 2)，合法返回 (值, None)。"""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        print(f"[run] --{label} 不是合法 JSON：{exc}", file=sys.stderr)
        return None, 2


def cmd_run_start(args) -> int:
    """启动一次运行；命中人工卡点属**正常状态** → exit 0 并打印结构化 JSON。"""
    from .agents.coordinator import CheckpointUnavailable, Coordinator

    code = _require_ledger(args.db)
    if code is not None:
        return code
    context: dict = {}
    if args.conflicts:
        context["caliber_conflicts"] = True
        if args.conflict_detail:
            detail, err = _parse_review_json(args.conflict_detail, "conflict-detail")
            if err is not None:
                return err
            context["conflicts"] = detail
        else:
            context["conflicts"] = _DEFAULT_CONFLICTS_REVIEW
    if args.sanity_blocking:
        context["sanity_blocking"] = True
        if args.sanity_detail:
            detail, err = _parse_review_json(args.sanity_detail, "sanity-detail")
            if err is not None:
                return err
            context["sanity_items"] = detail
        else:
            context["sanity_items"] = _DEFAULT_SANITY_REVIEW
    try:
        coord = Coordinator(db_path=args.db, ckpt_path=args.ckpt)
    except CheckpointUnavailable as exc:
        print(f"[run start] {exc}", file=sys.stderr)
        return 3
    try:
        status = coord.start(args.run_id, context)
    except CheckpointUnavailable as exc:
        print(f"[run start] {exc}", file=sys.stderr)
        return 3
    finally:
        coord.close()
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0


def cmd_run_resume(args) -> int:
    """投递人工裁定恢复；裁定非法/投错 gate → exit 2；台账库未初始化/检查点库不可用 → exit 3。"""
    from .agents.coordinator import CheckpointUnavailable, Coordinator, InvalidGateDecision

    code = _require_ledger(args.db)
    if code is not None:
        return code
    try:
        decision = json.loads(args.decision)
    except json.JSONDecodeError as exc:
        print(f"[run resume] decision 不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    try:
        coord = Coordinator(db_path=args.db, ckpt_path=args.ckpt)
    except CheckpointUnavailable as exc:
        print(f"[run resume] {exc}", file=sys.stderr)
        return 3
    try:
        status = coord.resume(args.run_id, args.gate, decision)
    except InvalidGateDecision as exc:
        print(f"[run resume] {exc}", file=sys.stderr)
        return 2
    except CheckpointUnavailable as exc:
        print(f"[run resume] {exc}", file=sys.stderr)
        return 3
    finally:
        coord.close()
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0


def cmd_run_status(args) -> int:
    """打印当前阶段与是否挂起（不推进图）；run_id 不存在 → exit 4。"""
    from .agents.coordinator import CheckpointUnavailable, Coordinator, UnknownRunError

    code = _require_ledger(args.db)
    if code is not None:
        return code
    try:
        coord = Coordinator(db_path=args.db, ckpt_path=args.ckpt)
    except CheckpointUnavailable as exc:
        print(f"[run status] {exc}", file=sys.stderr)
        return 3
    try:
        status = coord.status(args.run_id)
    except UnknownRunError as exc:
        print(f"[run status] {exc}", file=sys.stderr)
        return 4
    except CheckpointUnavailable as exc:
        print(f"[run status] {exc}", file=sys.stderr)
        return 3
    finally:
        coord.close()
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- FR-5 求解（solve）

def cmd_solve(args) -> int:
    """FR-5 求解入口（P0-2）：生成 → 守卫 → 执行 →（可选）修复；stdout 打印结构化 JSON。

    退出码契约：成功 0；否则 5（**不把失败伪装成 0**）；输入/spec 非法 → 2
      - 2：`--spec` 文件不存在 / 非法 JSON；`inputs` 中的输入文件缺失
      - 5：生成失败、静态守卫失败、执行失败（`run.ok=False`），**或 DoD 不达标**
        （`dod.ok=False`，如缺 `results/summary.csv` / 无 `dpi≈300` 的 PNG）

    两口径（task-007 P4 起）：stdout JSON 同时暴露
      - `ok`  = `run.ok`（`rc==0 ∧ manifest 存在 ∧ manifest 声明的文件齐`，向后兼容）
      - `dod` = 规划书 §4.2.2/§8.3 的 P0-2 DoD（直接核对 workdir 实际产物）
    **退出码以 DoD 为准**：仅当 `run.ok ∧ dod.ok` 才返回 0。

    `--max-attempts` 语义：`repair_rounds = max(0, N-1)`；**N=1 → repair_rounds=0 →
    不启用修复轮**（首轮失败即 5，不被修复掩盖）。
    """
    from .agents import coder
    from .tools import solve_tool

    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"[solve] spec 文件不存在：{spec_path}", file=sys.stderr)
        return 2
    try:
        spec_json = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[solve] spec 读取/解析失败：{exc}", file=sys.stderr)
        return 2

    workdir = Path(args.workdir)
    raw_inputs = list(spec_json.get("inputs") or [])
    try:
        staged = solve_tool.stage_inputs(raw_inputs, workdir) if raw_inputs else []
    except FileNotFoundError as exc:
        print(f"[solve] 输入文件缺失：{exc}", file=sys.stderr)
        return 2

    task_spec = {k: v for k, v in spec_json.items() if k != "inputs"}
    task_spec["inputs"] = staged
    # N7：把**原始**文件名（非 staging 后的 att1.xls）交给 schema 装配推导 table_id
    if not task_spec.get("source_file") and raw_inputs:
        task_spec["source_file"] = str(raw_inputs[0])

    repair_rounds = max(0, int(args.max_attempts) - 1)
    # R4：一次 `cli solve` **只产生一个 run_id**，贯穿首轮生成、守卫失败重试与修复轮
    # （`metric_obs.run_id` 因此能回答「这次 solve 一共调了几次模型」；此前最坏 4 个 id）。
    run_id = uuid.uuid4().hex
    runner_obj = coder.make_runner(run_id=run_id)
    regen = coder.make_regen_fn(runner=runner_obj, base_spec=task_spec)

    def gen_fn(spec: dict) -> dict:
        """绑定同一 runner 的 gen_fn（裸函数会自建 runner → 新 run_id → 预算计数散架）。"""
        return coder.generate_script(spec, runner=runner_obj)

    try:
        result = solve_tool.solve(
            task_spec, workdir,
            gen_fn=gen_fn, regen_fn=regen,
            timeout_s=int(args.timeout_s), repair_rounds=repair_rounds,
            auto_schema=(not args.no_auto_schema),
            schema_report_path=(args.schema_report or None),
        )
    except Exception as exc:  # noqa: BLE001  生成/守卫/执行异常一律按失败上报
        print(f"[solve] 求解异常：{type(exc).__name__}: {exc}", file=sys.stderr)
        print(json.dumps({
            "ok": False, "dod": None, "returncode": None,
            "data_schema_status": "error",
            "repaired": None, "script_path": None, "produced_files": [],
            "warnings": [f"{type(exc).__name__}: {exc}"], "duration_s": None,
        }, ensure_ascii=False))
        return 5

    run = result["run"]
    dod_report = result.get("dod") or {}
    out = {
        "ok": bool(run.get("ok")),
        "dod": dod_report,
        "returncode": run.get("returncode"),
        "data_schema_status": result.get("data_schema_status"),
        "repaired": result.get("repaired"),
        "script_path": result.get("script_path"),
        "produced_files": run.get("produced_files", []),
        "warnings": result.get("warnings", []),
        "duration_s": run.get("duration_s"),
    }
    print(json.dumps(out, ensure_ascii=False))
    # 退出码以 DoD 为准：run.ok 与 dod.ok 同时成立才算成功。
    return 0 if (out["ok"] and bool(dod_report.get("ok"))) else 5


# ---------------------------------------------------------------- 本地服务（serve）

# 仅回环 host 属「安全默认」；非回环须显式 `--allow-lan`（README §3.8）。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# `--no-static` 时传给 `create_app` 的「保证不存在」目录 → 触发其
# 「目录存在才挂载」分支，无需为关闭静态另开 API 开关。
_NO_STATIC_DIR = REPO_ROOT / "frontend" / ".serve-no-static"


def cmd_serve(args) -> int:
    """启动本地 HTTP 服务（API + 静态前端）。

    守卫（README §3.8）：
    - **默认仅回环**：`--host` 非 `127.0.0.1`/`localhost`/`::1` 且未给 `--allow-lan`
      → 打印明确拒因并 **exit 2**（不启动）；
    - `--allow-lan` 才允许非回环（并在 stderr 明确警告）；
    - `frontend/` 不存在且未给 `--no-static` → **exit 2**。

    `--db` / `--ckpt` 作为本次服务的**默认**台账库 / 检查点库（写入 `app.state`，
    `run` 路由在请求未显式给 `db`/`ckpt` 时取用）。
    """
    host = args.host
    if host not in _LOOPBACK_HOSTS and not args.allow_lan:
        print(f"[serve] 拒绝启动：--host {host} 非回环地址，且未给 --allow-lan。"
              f"如确需对外监听，请显式加 --allow-lan（服务将暴露给局域网/公网）。",
              file=sys.stderr)
        return 2
    if args.allow_lan and host not in _LOOPBACK_HOSTS:
        print(f"[serve] 警告：正在监听非回环地址 {host}，服务将对局域网/公网可见。",
              file=sys.stderr)

    frontend_dir = REPO_ROOT / "frontend"
    if not args.no_static and not frontend_dir.is_dir():
        print("[serve] 静态目录 frontend/ 不存在：请先交付 frontend/，"
              "或用 --no-static 仅启动 API。", file=sys.stderr)
        return 2

    from .app import create_app

    app = create_app(static_dir=(_NO_STATIC_DIR if args.no_static else frontend_dir))
    app.state.default_db = args.db
    app.state.default_ckpt = args.ckpt
    static_note = "未挂载（--no-static）" if args.no_static else f"{frontend_dir}"
    print(f"[serve] host={host} port={args.port} 静态目录={static_note} "
          f"API 前缀=/api/v1")
    print(f"http://127.0.0.1:{args.port}/")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - 依赖缺失属环境问题
        print(f"[serve] 缺少 uvicorn（{exc}）；请先安装依赖。", file=sys.stderr)
        return 3
    uvicorn.run(app, host=host, port=args.port, log_level="info")
    return 0


# ---------------------------------------------------------------- P0 验收命令（A2，规划书 §4.2）

def cmd_re_plot(args) -> int:
    """FR-6 P0-3：`cli re-plot` —— 由结果表 + figure specs 重出图。

    退出码：0=成功且每图 dpi==300、无 warnings、未超时；2=入参/输入非法；
    5=已出图但不达标（dpi≠300 / 有 warnings / 超时）。
    """
    import time

    import pandas as pd

    from .tools.plot_tool import render as plot_render

    started = time.monotonic()
    tables: dict[str, pd.DataFrame] = {}
    try:
        for item in args.result_table:
            table_id, sep, raw = item.partition("=")
            if not sep:
                table_id, raw = Path(item).stem, item
            if table_id in tables:
                print(f"[re-plot] 表 id 重复：{table_id}", file=sys.stderr)
                return 2
            tables[table_id] = pd.read_csv(raw)
    except (OSError, ValueError) as exc:
        print(f"[re-plot] 结果表读取失败：{exc}", file=sys.stderr)
        return 2

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"[re-plot] manifest 不存在：{manifest_path}", file=sys.stderr)
        return 2
    try:
        specs = json.loads(manifest_path.read_text(encoding="utf-8")) \
            if manifest_path.suffix.lower() == ".json" else yaml.safe_load(
                manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"[re-plot] manifest 解析失败：{exc}", file=sys.stderr)
        return 2
    if isinstance(specs, dict) and "figures" in specs:
        specs = specs["figures"]
    if not isinstance(specs, list) or not all(isinstance(s, dict) for s in specs):
        print("[re-plot] manifest 结构非法：应为列表或 {\"figures\":[...]}，元素为对象",
              file=sys.stderr)
        return 2
    for s in specs:
        if s.get("kind") not in ("line", "bar", "scatter", "hist"):
            print(f"[re-plot] kind 非法：{s.get('kind')!r}（可选 line/bar/scatter/hist）",
                  file=sys.stderr)
            return 2
        if s.get("table") not in tables:
            print(f"[re-plot] spec 引用的表不在 --result-table 里：{s.get('table')!r}",
                  file=sys.stderr)
            return 2

    try:
        report = plot_render(tables, specs, args.out)
    except (OSError, ValueError, KeyError) as exc:
        print(f"[re-plot] 出图失败：{exc}", file=sys.stderr)
        return 2
    elapsed = time.monotonic() - started
    dpi_ok = bool(report["figures"]) and all(f["dpi"] == 300 for f in report["figures"])
    warnings = list(report["warnings"])
    ok = dpi_ok and not warnings and elapsed <= args.timeout_s
    payload = {"ok": ok, "out_dir": str(Path(args.out).resolve()),
               "elapsed_s": round(elapsed, 3), "timeout_s": args.timeout_s,
               "figure_manifest_csv": report["manifest_path"],
               "figures": report["figures"], "dpi_ok": dpi_ok,
               "warnings": warnings,
               "tables": {k: str(Path(v).resolve())
                          for k, v in _re_plot_table_paths(args).items()}}
    print(json.dumps(payload, ensure_ascii=False))
    if not ok:
        print("[re-plot] 已出图但不达标（dpi≠300 / 有 warnings / 超时），见上方 JSON。",
              file=sys.stderr)
        return 5
    return 0


def _re_plot_table_paths(args) -> dict[str, str]:
    """还原「表 id → CSV 路径」映射（仅用于 stdout 证据）。"""
    out: dict[str, str] = {}
    for item in args.result_table:
        table_id, sep, raw = item.partition("=")
        if not sep:
            table_id, raw = Path(item).stem, item
        out[table_id] = raw
    return out


def cmd_profile_show(args) -> int:
    """FR-9 P0-4：`cli profile show` —— 读取档案版本并打印/落盘固定 8 键 JSON。"""
    from .tools import profile_tool

    if args.version != "last":
        try:
            version: str | int = int(args.version)
        except ValueError:
            print(f"[profile show] --version 非法：{args.version!r}（应为 last 或整数）",
                  file=sys.stderr)
            return 2
    else:
        version = "last"
    try:
        data = profile_tool.read(args.profile, version, db_path=args.db)
    except KeyError as exc:
        print(f"[profile show] 档案或版本不存在：{exc}", file=sys.stderr)
        return 2
    text = json.dumps(data, ensure_ascii=False)
    if args.json and args.json != "-":
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_profile_trace(args) -> int:
    """FR-9 A4：`cli profile trace` —— 打印档案字段级溯源（单 JSON，exit 0/2）。

    `--version last` 复用 `store.profile.read_profile` 的既有口径（优先最新 adopted），
    不另造一套「最新版本」定义，保证与 `profile show` 对同一档案解析出同一 version_id。
    """
    from .store import profile as profile_store
    from .store.field_trace import list_field_traces

    try:
        version: int | str = ("last" if args.version == "last" else int(args.version))
    except ValueError:
        print(f"[profile trace] --version 非法：{args.version!r}（应为 last 或整数）",
              file=sys.stderr)
        return 2
    if args.limit < 0:
        print(f"[profile trace] --limit 非法：{args.limit}（应为非负整数）", file=sys.stderr)
        return 2
    try:
        version_id = int(profile_store.read_profile(
            args.profile, version, db_path=args.db)["version_id"])
        result = list_field_traces(args.profile, version_id, args.field,
                                   limit=args.limit, db_path=args.db)
    except KeyError as exc:
        print(f"[profile trace] 档案或版本不存在：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[profile trace] 参数非法：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({"profile_id": args.profile, "version_id": version_id,
                      "rows": result["rows"], "count": result["count"],
                      "total": result["total"]}, ensure_ascii=False))
    return 0


def cmd_problem_parse(args) -> int:
    """FR-1：`cli problem parse` —— 题面解析并写入 **draft** 档案版本。

    退出码：0=成功；2=输入非法（文件不存在 / 题面为空）；5=LLM 调用失败（含重试与降级）。
    """
    from .agents import archivist
    from .agents.base import LLMCallError

    if args.text == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.text)
        if not path.is_file():
            print(f"[problem parse] 题面文件不存在：{args.text}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        print("[problem parse] 题面为空，拒绝解析", file=sys.stderr)
        return 2

    try:
        runner = archivist.make_runner(db_path=args.db)
        parsed = archivist.parse_problem(text, runner=runner, profile_id=args.profile_id,
                                        actor=args.actor, db_path=args.db)
    except LLMCallError as exc:
        print(f"[problem parse] 解析失败：{exc}", file=sys.stderr)
        return 5

    written = archivist.write_profile_from_parse(parsed, args.profile_id, args.actor,
                                                 db_path=args.db)
    if args.json_out:
        print(json.dumps({**written, "parse": parsed}, ensure_ascii=False))
    else:
        print(json.dumps(written, ensure_ascii=False))
    return 0


def cmd_plan_build(args) -> int:
    """FR-3：`cli plan build` —— 题面 → 方案卡片（≥3 个候选方法），落 `artifacts/plan/`。

    退出码：0=成功（产物已落盘并登记 `artifacts`）；2=输入非法（文件不存在 / 题面为空）；
    3=写盘失败；5=LLM 调用失败（含重试与降级）或输出过不了严格闸（候选 <3 / 字段缺 / 方法重名）。

    说明：`valid=False` / `ready_for_gate=False` **仍是 exit 0**——本命令只产出「交人裁的候选」，
    是否采用由 PLAN 人工卡点决定（`docs/REMAINING_PLAN.md` §2.2 C1）。这两项在 stderr 提示，
    并原样写进 JSON，调用方（含前端）自行判断，不由 CLI 替人做决定。
    """
    from .agents import planner
    from .agents.base import LLMCallError

    if args.text == "-":
        text = sys.stdin.read()
    else:
        path = Path(args.text)
        if not path.is_file():
            print(f"[plan build] 题面文件不存在：{args.text}", file=sys.stderr)
            return 2
        text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        print("[plan build] 题面为空，拒绝生成方案卡片", file=sys.stderr)
        return 2

    try:
        plan = planner.generate_plan(
            text,
            profile_id=(args.profile_id or None),
            db_path=args.db,
            out_dir=(args.out_dir or planner.PLAN_ARTIFACT_DIR),
            run_id=(args.run_id or None),
        )
    except LLMCallError as exc:
        print(f"[plan build] 方案卡片生成失败：{exc}", file=sys.stderr)
        return 5
    except OSError as exc:
        print(f"[plan build] 写盘失败：{exc}", file=sys.stderr)
        return 3

    if not plan.get("valid", False):
        print(f"[plan build] 注意：候选存在不完整字段（valid=false），"
              f"请人工核对后再用于 PLAN 卡点", file=sys.stderr)
    if not plan.get("ready_for_gate", False):
        print(f"[plan build] 注意：ready_for_gate=false"
              f"（fabrication_check={plan.get('fabrication_check')!r}），"
              f"不建议直接放行", file=sys.stderr)

    if args.json_out:
        print(json.dumps(plan, ensure_ascii=False))
    else:
        print(json.dumps({
            "run_id": plan.get("run_id"),
            "plan_path": plan.get("plan_path"),
            "artifact_id": plan.get("artifact_id"),
            "candidates": [t.get("method") for t in plan.get("tasks") or []],
            "valid": plan.get("valid"),
            "ready_for_gate": plan.get("ready_for_gate"),
            "fabrication_check": plan.get("fabrication_check"),
            "open_questions": plan.get("open_questions") or [],
        }, ensure_ascii=False))
    return 0


def cmd_paper_build(args) -> int:
    """FR-7：`cli paper build` —— 档案 → 五章 docx 骨架 + sidecar manifest。

    **纯确定性装配，不调 LLM**（README §0-R1）。退出码：
    0=成功；2=输入非法（档案/版本不存在、`--version` 非 last/整数）；
    3=写盘失败（目录不存在等，沿用 CLI 既有「运行期/IO 不可用」码 3）。
    """
    from .tools import paper_tool

    if args.version != "last":
        try:
            version: str | int = int(args.version)
        except ValueError:
            print(f"[paper build] --version 非法：{args.version!r}（应为 last 或整数）",
                  file=sys.stderr)
            return 2
    else:
        version = "last"
    try:
        result = paper_tool.build_paper(
            profile_id=args.profile_id, version=version,
            figures_dir=(args.figures_dir or None),
            results_dir=(args.results_dir or None),
            out_path=args.out, actor=args.actor, db_path=args.db)
    except KeyError as exc:
        print(f"[paper build] 档案或版本不存在：{exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"[paper build] 写盘失败：{exc}", file=sys.stderr)
        return 3

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[paper build] out={result['out_path']} "
              f"paragraphs={result['paragraphs']} figures={result['figures']} "
              f"placeholders={result['placeholders']} "
              f"appendix_code={result['appendix_code']} "
              f"manifest={result['manifest_path']}")
    return 0


def cmd_export_build(args) -> int:
    """FR-12：`cli export build` —— paper.docx → 可交付目录。

    页脚写 `本文由 AI 辅助生成，已由 {reviewer} 人工审核`（写在**副本**上，源文件不动），
    并产出 `manifest.json` + `SHA256SUMS`。

    退出码（本命令新增 4/6，与既有约定并列）：
    - 0 成功；
    - 2 `--version` 非法（既有 fail-closed 约定）；
    - 3 写盘失败（既有约定：运行期/IO 不可用）；
    - 4 `reviewer` 缺失/纯空白/单字符（§R4 必填）；
    - 6 被阻断（paper 缺失 / manifest 不符 / 档案 draft / open_questions 非空 /
      文件数 <10 / 缺合规声明 / 结果表越界；后三者各有 `--allow-*` 可显式放行）。
    """
    from .tools import export_tool

    if args.version != "last":
        try:
            version: str | int = int(args.version)
        except ValueError:
            print(f"[export build] --version 非法：{args.version!r}（应为 last 或整数）",
                  file=sys.stderr)
            return 2
    else:
        version = "last"
    try:
        result = export_tool.build_deliverable(
            paper_docx=args.paper,
            figures_dir=(args.figures_dir or None),
            results_dir=(args.results_dir or None),
            compliance_dir=(args.compliance_dir or None),
            out_dir=args.out_dir, reviewer=args.reviewer,
            profile_id=(args.profile_id or None), version=version,
            allow_draft=args.allow_draft,
            allow_open_questions=args.allow_open_questions,
            allow_few_files=args.allow_few_files,
            allow_missing_disclosure=args.allow_missing_disclosure,
            allow_sanity=args.allow_sanity,
            db_path=args.db)
    except export_tool.ExportBlocked as exc:
        print(f"[export build] {exc}", file=sys.stderr)
        return 6
    except ValueError as exc:
        print(f"[export build] reviewer 非法：{exc}", file=sys.stderr)
        return 4
    except OSError as exc:
        print(f"[export build] 写盘失败：{exc}", file=sys.stderr)
        return 3

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[export build] out_dir={result['out_dir']} "
              f"files={len(result['files'])} total_bytes={result['total_bytes']} "
              f"reviewer={result['reviewer']} "
              f"paper_paragraphs={result['paper_paragraphs']} "
              f"figures={result['figures']}")
    return 0


def cmd_compliance_build(args) -> int:
    """FR-13 P0-5：`cli compliance build` —— 生成 AI 使用披露（md + docx）。"""
    from .tools import compliance_tool

    if args.version != "last":
        try:
            version_id: int | None = int(args.version)
        except ValueError:
            print(f"[compliance build] --version 非法：{args.version!r}（应为 last 或整数）",
                  file=sys.stderr)
            return 2
    else:
        version_id = None
    try:
        md_path = compliance_tool.make_disclosure(
            args.profile, version_id, args.out,
            human_edit_ratio=args.human_edit_ratio, db_path=args.db)
    except (OSError, KeyError, ValueError) as exc:
        print(f"[compliance build] 生成失败：{exc}", file=sys.stderr)
        return 5
    md_path, docx_path = Path(md_path), Path(md_path).with_suffix(".docx")
    if not (md_path.exists() and docx_path.exists() and md_path.stat().st_size > 0
            and docx_path.stat().st_size > 0):
        print(f"[compliance build] 产物缺失或为空：{md_path} / {docx_path}", file=sys.stderr)
        return 5
    print(json.dumps({"ok": True, "md_path": str(md_path.resolve()),
                      "docx_path": str(docx_path.resolve()),
                      "profile_id": args.profile, "version": args.version},
                     ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- FR-8 进度守卫
def cmd_progress_status(args) -> int:
    """FR-8 最小形态：`cli progress status` —— 倒计时 + 无产出预警 + 不做清单。

    **纯只读**：库以 `mode=ro` 打开（写语句会被 SQLite 拒绝），不写库、不联网、不调 LLM。
    阈值与清单常量全部在 `progress_tool` 里（可溯源到《需求说明书》），本命令只做参数
    转接与出参整形——**不在 CLI 层重新判一次阈值**，否则两处口径会漂移。

    退出码（本命令新增 4，口径是「有预警但**不是失败**」）：
    - 0 无预警；
    - 2 `--deadline` / `--now` 非法，或库文件不存在（fail-closed：不静默当空库）；
    - 4 有预警（`warnings` 非空），让人与脚本都能判「需要注意」而不必解析 JSON。
    """
    from .tools import progress_tool

    try:
        result = progress_tool.progress_status(
            db_path=args.db,
            deadline=(args.deadline or None),
            now=(args.now or None),
            profile_id=(args.profile_id or None))
    except FileNotFoundError as exc:
        print(f"[progress status] {exc}", file=sys.stderr)
        return 2
    except (ValueError, TypeError) as exc:
        # 注意：这里同时会接住「相对路径无法转 file URI」这类**非时间**错误，故文案不写死
        # 「时间参数非法」——历史上正是这个措辞掩盖了 `--db data/mm.db` 崩溃（D-55）。
        print(f"[progress status] 参数非法：{exc}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[progress status] now={result['now']} deadline={result['deadline']} "
              f"remaining_hours={result['remaining_hours']} idle_hours={result['idle_hours']} "
              f"stage={result['stage']} events={result['event_count']} "
              f"artifacts={result['artifact_count']} "
              f"warnings={len(result['warnings'])} "
              f"actionable_ratio={result['actionable_ratio']}")
        if args.profile_id and (result["unfiltered_event_count"] != result["event_count"]
                               or result["unfiltered_artifact_count"] != result["artifact_count"]):
            # 过滤口径与全库口径不同：必须把差值摆出来，否则「该档案 0 条」会被读成「库里没数据」
            print(f"  (过滤口径 profile_id={args.profile_id!r}；全库口径 "
                  f"events={result['unfiltered_event_count']} "
                  f"artifacts={result['unfiltered_artifact_count']})")
        for warn in result["warnings"]:
            print(f"  - [{warn['code']}] {warn['message']}")
            print(f"    action: {warn['action']}")
        if result["min_viable"]:
            min_viable = result["min_viable"]
            print(f"[progress status] 最短可交版本（{min_viable['source']}）：")
            for chapter in min_viable["chapters"]:
                print(f"  * {chapter}")
            for figure in min_viable["figures"]:
                print(f"  * {figure}")
        if result["not_do_list"]:
            print(f"[progress status] 不做清单（来源：{result['not_do_source']}）：")
            for item in result["not_do_list"]:
                print(f"  - {item['item']}（{item['why']}；生效：{item['when']}）")
    return 4 if result["warnings"] else 0


# ---------------------------------------------------------------- FR-10 一致性校验
def cmd_consistency_check(args) -> int:
    """FR-10：`cli consistency check` —— 论文正文数字 vs 结果文件。

    **纯读**：只读 `docx` 的 `doc.paragraphs` 与 `results_dir/**/*.csv`，不写任何文件、
    不调 LLM、不联网。口径（哪段算「正文」、什么算「非数据数字」）全部在
    `consistency_tool` 里，本命令只转接参数与退出码。

    退出码（本命令新增 6，与 `export build` 的 4/6 并列）：
    - 0 `status == ok`（有可校验数字且全部命中）
    - 2 参数/输入不可用（结果目录不存在或无 CSV、显式 sidecar 不存在/读不出）
    - 4 `status == mismatch`（有正文数字在结果表里找不到出处 —— **阻断**）
    - 6 `status ∈ {no_data, stale_sidecar}`（**没校验成，不等于通过**）：
      `no_data` = 正文没有可校验数字（全是占位/转述）；`stale_sidecar` = sidecar 已不覆盖
      当前 docx，校验基础过期，需重跑 `paper build` 或人工确认。
    """
    from .tools import consistency_tool

    try:
        report = consistency_tool.check_consistency(
            paper_docx=args.paper,
            results_dir=args.results_dir,
            sidecar=(args.sidecar or None),
            include_appendix=args.include_appendix,
            ignore_tokens=tuple(args.ignore_token or ()))
    except FileNotFoundError as exc:
        print(f"[consistency check] {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[consistency check] {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"[consistency check] status={report['status']} "
              f"consistent={report['consistent']} "
              f"paper_numbers={report['paper_numbers']} "
              f"result_numbers={report['result_numbers']} "
              f"matched={report['matched']} loose={report['loose_matched']} "
              f"unmatched={len(report['unmatched'])} "
              f"checked_paragraphs={report['checked_paragraphs']} "
              f"stale_sidecar={report['stale_sidecar']}")
        # 表格计数**必须**上屏（D-79b）：只打段落时，「表被改过但没有无出处数字」
        # 的论文在文本模式下完全没有信号（status 只到 no_data，看不出表被查过）。
        print(f"  tables checked={report['checked_tables']} "
              f"registered={report['registered_tables']} "
              f"tampered_tables={report['tampered_tables']} "
              f"format_only={report['format_changed_tables']}")
        for item in report["unmatched"]:
            # 表格条目没有 paragraph_index（保持 None 以避免下游 KeyError），
            # 无条件打印 `段{None}` 会得到「段None: x1」这种读者无法定位的丑行。
            if item.get("kind") == "table" or "table" in item:
                where = (f"表{item['table']} "
                         f"第{item['row'] + 1}行第{item['col'] + 1}列")
            else:
                where = f"{item['chapter']}  段{item['paragraph_index']}"
            print(f"  ! {item['token']!r}  {where}: {item['preview']}")
        if report["stale_sidecar"]:
            # `stale_sidecar` 与 `status` 正交：即使 status=ok（数字全命中），sidecar 过期也要说
            print(f"  ! sidecar 已不覆盖当前 docx（已照常校验，建议重跑 paper build 对齐）："
                  f"未登记段落={report['unrecorded_paragraphs']} "
                  f"被改写段落={report['tampered_paragraphs']}")
        if report["legacy_sidecar"]:
            # 旧格式 sidecar（整份缺 text_sha256）只能按下标匹配，等于放弃了"是否被改写"的判断
            print("  ! 该 sidecar 为旧格式（条目缺 text_sha256）：已退回按下标匹配，"
                  "**无法判断段落是否被人改写**，结果请按较低把握采信")
        if report["cjk_numeral_paragraphs"]:
            print(f"  ! 以下段落有中文数字但抽不出阿拉伯数字 token，本工具读不到，请人工确认："
                  f"{report['cjk_numeral_paragraphs']}")
        if report["status"] == "no_data":
            print(f"  ! 无可校验正文数字；排除计数 {report['excluded']}")

    if report["status"] == "mismatch":
        return 4
    if report["status"] in {"no_data", "stale_sidecar"}:
        return 6
    return 0


# ---------------------------------------------------------------- FR-14 档案库
def cmd_archive_ingest(args) -> int:
    """FR-14：`cli archive ingest` —— manifest → `archive_item`（按 `archive_id` 幂等）。

    素材已抓取落盘（`artifacts/exam_corpus/`），本命令**只读本地文件**、不联网。
    口径全部在 `tools/archive_tool.py`，本命令只转接参数与退出码。

    退出码：
    - 0 全部落库且**每条素材在磁盘上都找得到**（`--verify-sha` 时主体 + 附件的哈希也全对）；
    - 2 manifest 不可用（缺失/JSON 坏/schema_version 不符/字段非法/路径穿越）；
    - 4 **部分落空**：有 `file_missing` / `page_missing` / `attachment_missing` /
      `sha_mismatch` —— 行已入库但素材不完整，必须让 CI 看见（不能因为「入库成功」就报 0）。
    """
    from .tools import archive_tool

    try:
        result = archive_tool.ingest_manifest(
            args.manifest, db_path=args.db, verify_sha=args.verify_sha,
            root=(args.root or None))
    except FileNotFoundError as exc:
        print(f"[archive ingest] {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[archive ingest] manifest 非法：{exc}", file=sys.stderr)
        return 2

    counts = result["counts"]
    print(f"[archive ingest] manifest={result['manifest']} "
          f"schema_version={result['schema_version']} "
          f"total={counts['total']} exam_problem={counts['exam_problem']} "
          f"paper={counts['paper']} inserted={result['inserted']} "
          f"updated={result['updated']} verified_sha={result['verified_sha']}")
    print(f"  file_missing={len(result['file_missing'])} {result['file_missing']}")
    print(f"  page_missing={len(result['page_missing'])} {result['page_missing']}"
          f"（论文页级，共声明 {result['page_total']} 页）")
    print(f"  attachment_missing={len(result['attachment_missing'])} "
          f"{result['attachment_missing']}")
    print(f"  sha_mismatch={len(result['sha_mismatch'])} {result['sha_mismatch']}")
    print(f"  skipped_attachment_count={result['skipped_attachment_count']}"
          "（按设计未抓取，不参与校验）")
    return 4 if (result["file_missing"] or result["page_missing"]
                 or result["attachment_missing"] or result["sha_mismatch"]) else 0


def cmd_archive_list(args) -> int:
    """FR-14：`cli archive list` —— 过滤 + 分页，`file_present` 为**现算**值。

    退出码：0 查询成功（空结果也是 0）；2 参数非法（如 `--kind exam`）或库不可用。
    """
    from .tools import archive_tool

    if not Path(args.db).exists():
        print(f"[archive list] 库不存在：{args.db}"
              "（本命令不会创建空库；请先跑 archive ingest）", file=sys.stderr)
        return 2
    try:
        result = archive_tool.list_archives(
            year=args.year, kind=(args.kind or None), q=(args.q or None),
            limit=args.limit, offset=args.offset, db_path=args.db,
            root=(args.root or None))
    except (ValueError, FileNotFoundError) as exc:
        print(f"[archive list] {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    print(f"[archive list] count={result['count']} total={result['total']} "
          f"limit={args.limit} offset={args.offset}")
    header = (f"{'archive_id':22} {'kind':12} {'year':4} {'code':4} {'tag':6} "
              f"{'bytes':>10} {'pages':>5} {'file':4} {'att':>3} {'pgmiss':>6} title")
    print(header)
    for row in result["rows"]:
        title = (row.get("title") or "")[:40]
        print(f"{row['archive_id']:22} {row['kind']:12} {row['year']:<4} "
              f"{(row.get('problem_code') or '-'):4} {(row.get('tag') or '-'):6} "
              f"{(row.get('bytes') if row.get('bytes') is not None else 0):>10} "
              f"{(row.get('page_count') if row.get('page_count') is not None else '-'):>5} "
              f"{('在' if row['file_present'] else '缺失'):4} "
              f"{row['attachments_missing']:>3} {row['pages_missing']:>6} {title}")
    return 0


def cmd_archive_summary(args) -> int:
    """FR-14：`cli archive summary` —— 统计 + 文件缺失数 + manifest 存在性。

    退出码：0 统计成功；2 库不可用（`file_missing > 0` **不改退出码**：统计本身成功，
    素材完整性请用 `archive ingest` 的退出码判定）。
    """
    from .tools import archive_tool

    # 库不存在就**不建空库**：`connect()` 会自动 `ensure_schema()`，于是「路径写错」会被
    # 报成「库里 0 条」并 exit 0（gk-r27 实测）。空库与错路径必须分开。
    if not Path(args.db).exists():
        print(f"[archive summary] 库不存在：{args.db}"
              "（本命令不会创建空库；请先跑 archive ingest）", file=sys.stderr)
        return 2
    try:
        result = archive_tool.archive_summary(db_path=args.db, root=(args.root or None))
    except FileNotFoundError as exc:
        print(f"[archive summary] {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    by_kind = result["by_kind"]
    print(f"[archive summary] total={result['total']} "
          f"exam_problem={by_kind['exam_problem']} paper={by_kind['paper']} "
          f"years={result['years']} file_missing={result['file_missing']} "
          f"page_total={result['page_total']} page_missing={result['page_missing']} "
          f"attachment_total={result['attachment_total']} "
          f"attachment_missing={result['attachment_missing']} "
          f"manifest_present={result['manifest_present']}")
    for row in result["by_year"]:
        print(f"  {row['year']}: total={row['total']} "
              f"exam_problem={row['exam_problem']} paper={row['paper']}")
    if result["file_missing"]:
        print(f"  ! 主体文件缺失（{result['file_missing']} 条，最多列 20 条）："
              f"{result['file_missing_ids']}")
    if result["page_missing"]:
        # 论文页级缺失必须单独吼一声：目录在、少一页时 file_missing 仍是 0（假绿灯）
        print(f"  ! 论文页缺失（{result['page_missing']} 页 / 共声明 "
              f"{result['page_total']} 页，最多列 20 条）：{result['page_missing_ids']}")
    if result["attachment_missing"]:
        # 附件口径必须单独吼一声：附件全丢时 file_missing 仍会是 0（假绿灯）
        print(f"  ! 附件缺失（{result['attachment_missing']} 项 / 共 "
              f"{result['attachment_total']} 项，最多列 20 条）："
              f"{result['attachment_missing_ids']}")
    print(f"  manifest: {result['manifest_path']}")
    return 0


# ---------------------------------------------------------------- NFR-4 golden（B1）
# 契约：.agent-runs/2026-10-17/task-003/README.md §3.3~§3.6。
# 口径：golden = **本产线在冻结输入下的确定性产物 sha256**（§3.1 裁定 / D-15），
# 不是规划书 §4.2.1 的 row_count 抽检值（该三值已穷举证伪，不得写入任何代码或数据）。

class GoldenConfigError(RuntimeError):
    """基线 / case 配置不可用 —— 一律 exit 2，**不得**静默跳过（README §2 第 4 条）。"""


def _load_golden_cases() -> list[dict]:
    """读取并校验 `tests/golden/cases.json`，返回 case 列表（顺序即文件顺序）。"""
    if not GOLDEN_CASES.exists():
        raise GoldenConfigError(f"case 注册表缺失：{GOLDEN_CASES}")
    try:
        data = json.loads(GOLDEN_CASES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenConfigError(f"{GOLDEN_CASES} JSON 解析失败：{exc}") from exc
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise GoldenConfigError(f"{GOLDEN_CASES}：`cases` 必须是非空数组")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise GoldenConfigError(f"{GOLDEN_CASES}：cases[{index}] 不是对象")
        cid = case.get("id")
        if not isinstance(cid, str) or not cid:
            raise GoldenConfigError(f"{GOLDEN_CASES}：cases[{index}].id 缺失或非字符串")
        if cid in seen:
            raise GoldenConfigError(f"{GOLDEN_CASES}：case id 重复：{cid}")
        seen.add(cid)
        outputs = case.get("outputs")
        if not isinstance(outputs, list) or not outputs or \
                not all(isinstance(o, str) and o for o in outputs):
            raise GoldenConfigError(f"{GOLDEN_CASES}：case {cid} 的 `outputs` 必须是非空字符串数组")
        producer = case.get("producer")
        argv = producer.get("argv") if isinstance(producer, dict) else None
        if not isinstance(argv, list) or not argv:
            raise GoldenConfigError(f"{GOLDEN_CASES}：case {cid} 的 `producer.argv` 必须是非空数组")
        n_expected = case.get("n_expected")
        if not isinstance(n_expected, int) or n_expected <= 0:
            raise GoldenConfigError(f"{GOLDEN_CASES}：case {cid} 的 `n_expected` 必须是正整数")
        caliber = case.get("hash_caliber")
        if caliber != HASH_CALIBER:
            raise GoldenConfigError(
                f"{GOLDEN_CASES}：case {cid} 的 `hash_caliber` 必须是 {HASH_CALIBER!r}"
                f"（实际 {caliber!r}）；口径不符时基线与本树不可比，拒绝继续")
    return cases


def _select_cases(cases: list[dict], case_id: str) -> list[dict]:
    """按 `--case` 过滤；未知 id → 配置错误（不许静默跑全部或跳过）。"""
    if not case_id:
        return cases
    picked = [c for c in cases if c["id"] == case_id]
    if not picked:
        known = ", ".join(c["id"] for c in cases)
        raise GoldenConfigError(f"未知 case id：{case_id}（已注册：{known}）")
    return picked


def _load_golden_sums() -> dict[str, str]:
    """读取 `tests/golden/SHA256SUMS`；缺失/非法 → 配置错误（spotcheck 不得返回 0）。"""
    if not GOLDEN_SUMS.exists():
        raise GoldenConfigError(
            f"基线缺失：{GOLDEN_SUMS}（先跑 `cli freeze-golden --write` 生成，不许手工录入）")
    try:
        return load_sums(GOLDEN_SUMS)
    except (ValueError, OSError) as exc:
        raise GoldenConfigError(f"{GOLDEN_SUMS} 解析失败：{exc}") from exc


def _expected_for_case(all_sums: dict[str, str], case: dict) -> dict[str, str]:
    """从全量基线里取出属于本 case `outputs` 前缀的行。"""
    prefixes = tuple(f"{out.rstrip('/')}/" for out in case["outputs"])
    return {k: v for k, v in all_sums.items() if k.startswith(prefixes)}


def _resolve_dataset(producer: dict) -> Path:
    """`{dataset}` 解析：环境变量 → 默认路径；两者都不可用 → exit 2（报文含 env 名与默认值）。"""
    env_name = str(producer.get("dataset_env") or "")
    default = str(producer.get("dataset_default") or "")
    tried: list[str] = []
    if env_name:
        raw = os.environ.get(env_name, "")
        if raw and Path(raw).is_dir():
            return Path(raw)
        tried.append(f"{env_name}={raw or '<未设置>'}")
    if default:
        if Path(default).is_dir():
            return Path(default)
        tried.append(f"dataset_default={default}")
    raise GoldenConfigError(
        "数据集不可用：环境变量 "
        f"{env_name or '<未配置>'} 与默认路径 {default or '<未配置>'} 均不可用"
        f"（已尝试：{'; '.join(tried) or '<无>'}）。"
        "可用环境变量覆盖，或修正 tests/golden/cases.json 的 dataset_default。")


def _run_producer(case: dict, dataset: Path, out_dir: Path) -> None:
    """进程内跑产线（**不 spawn 子进程**）；未知 kind → 配置错误（fail-closed）。"""
    argv = case["producer"]["argv"]
    kind = case.get("kind")
    if argv[0] != kind:
        raise GoldenConfigError(
            f"case {case['id']}：producer.argv[0]={argv[0]!r} 与 kind={kind!r} 不一致")
    if kind != "intake":
        raise GoldenConfigError(
            f"case {case['id']}：未知 kind={kind!r}（当前只支持 intake，fail-closed 不跳过）")
    from .pipeline.intake import run_intake_audit

    run_intake_audit(dataset, out_dir)


def _actual_for_case(case: dict, out_dir: Path) -> dict[str, str]:
    """`{golden 相对路径: sha256}`：tmp 目录产物映射回首个 `outputs` 前缀。"""
    prefix = case["outputs"][0].rstrip("/")
    scanned = scan_files(out_dir, rel_to=out_dir)
    return {f"{prefix}/{name}": digest for name, digest in scanned.items()}


def _scan_case_outputs(root: Path, case: dict, actual: dict[str, str]) -> dict[str, str]:
    """在 `root/<outputs>` 下补扫实际存在的文件，用于捕获 `extra`（D-16：多出也算漂移）。"""
    for out in case["outputs"]:
        base = root / out
        if not base.is_dir():
            continue
        for rel, digest in scan_files(base, rel_to=root).items():
            actual.setdefault(rel, digest)
    return actual


def _print_delta(label: str, result: dict) -> None:
    for rel in result["missing"]:
        print(f"{label}   missing  {rel}")
    for rel in result["extra"]:
        print(f"{label}   extra    {rel}")
    for rel in result["modified"]:
        detail = result["detail"][rel]
        print(f"{label}   modified {rel}  {detail['expected'][:12]} → {detail['actual'][:12]}")


def _verdict(result: dict) -> str:
    return "PASS" if result["ok"] else "FAIL"


def _summary_line(label: str, result: dict) -> str:
    return (f"{label} matched={result['matched']} missing={len(result['missing'])} "
            f"extra={len(result['extra'])} modified={len(result['modified'])} "
            f"→ {_verdict(result)}")


def cmd_spotcheck(args) -> int:
    """只读比对：当前树 vs `tests/golden/SHA256SUMS`（不跑产线、不写盘）。"""
    from .metrics import sha256_file

    try:
        cases = _select_cases(_load_golden_cases(), args.case)
        all_sums = _load_golden_sums()
        root = Path(args.root).resolve() if args.root else REPO_ROOT
        if not root.is_dir():
            raise GoldenConfigError(f"--root 不是目录：{root}")
        results: list[dict] = []
        for case in cases:
            expected = _expected_for_case(all_sums, case)
            if len(expected) != case["n_expected"]:
                raise GoldenConfigError(
                    f"case {case['id']}：n_expected={case['n_expected']} 与基线行数 "
                    f"{len(expected)} 不一致（{GOLDEN_SUMS}）")
            actual: dict[str, str] = {}
            for rel in expected:
                path = root / rel
                if path.is_file():
                    actual[rel] = sha256_file(path)
            _scan_case_outputs(root, case, actual)
            results.append({**compare_sums(expected, actual), "case": case["id"]})
    except (GoldenConfigError, OSError) as exc:
        print(f"[spotcheck] 配置错误：{exc}", file=sys.stderr)
        return 2

    ok = all(item["ok"] for item in results)
    if args.json_out:
        print(json.dumps({"ok": ok, "cases": results}, ensure_ascii=False))
    else:
        for item in results:
            label = f"[spotcheck] {item['case']}"
            print(_summary_line(label, item))
            _print_delta(label, item)
        print(f"[spotcheck] root={root}")
    return 0 if ok else 1


def _freeze_one(case: dict, all_sums: dict[str, str], *, baseline: bool) -> dict:
    """跑一个 case：mkdtemp → 产线 → 实际哈希 → 与基线比对。"""
    dataset = _resolve_dataset(case.get("producer") or {})
    out_dir = Path(tempfile.mkdtemp(prefix=f"mmagent_golden_{case['id']}_"))
    _run_producer(case, dataset, out_dir)
    actual = _actual_for_case(case, out_dir)
    if len(actual) != case["n_expected"]:
        raise GoldenConfigError(
            f"case {case['id']}：产线产出 {len(actual)} 个文件，与 n_expected="
            f"{case['n_expected']} 不一致（{out_dir}）")
    expected = _expected_for_case(all_sums, case)
    if baseline and len(expected) != case["n_expected"]:
        raise GoldenConfigError(
            f"case {case['id']}：n_expected={case['n_expected']} 与基线行数 "
            f"{len(expected)} 不一致（{GOLDEN_SUMS}）")
    return {**compare_sums(expected, actual), "case": case["id"],
            "out": str(out_dir), "_expected": expected, "_actual": actual,
            "_outputs": list(case["outputs"])}


def cmd_freeze_golden(args) -> int:
    """重跑产线并与冻结基线比对。**默认 dry-run 不写任何文件**，`--write` 才落盘。"""
    try:
        cases = _select_cases(_load_golden_cases(), args.case)
        baseline = GOLDEN_SUMS.exists()
        all_sums = _load_golden_sums() if baseline else {}
        results = [_freeze_one(case, all_sums, baseline=baseline) for case in cases]
    except (GoldenConfigError, OSError) as exc:
        print(f"[freeze-golden] 配置错误：{exc}", file=sys.stderr)
        return 2

    ok = all(item["ok"] for item in results)
    merged: dict[str, str] | None = None
    if args.write:
        merged = dict(all_sums)
        for item in results:
            prefixes = tuple(f"{out.rstrip('/')}/" for out in item["_outputs"])
            merged = {k: v for k, v in merged.items() if not k.startswith(prefixes)}
            merged.update(item["_actual"])
    wrote = len(merged) if merged is not None else 0

    # 人读报文（含漂移明细）**一律先打印**，再执行 `--write` —— 防误刷基线（D-17）。
    if not args.json_out:
        for item in results:
            label = f"[freeze-golden] {item['case']}"
            print(_summary_line(label, item))
            print(f"{label} out={item['out']}")
            _print_delta(label, item)
        if not args.write:
            print("[freeze-golden] dry-run：未写盘（加 --write 才落盘）")
        elif not ok:
            print(f"[freeze-golden] OVERWRITE {GOLDEN_SUMS}（delta≠0，覆盖前明细见上）")

    payload = {"ok": ok, "write": bool(args.write), "wrote": wrote,
               "cases": [{k: v for k, v in item.items() if not k.startswith("_")}
                         for item in results]}
    if args.json_out:
        # stdout 只有一个对象；`wrote` 是**待写入行数**，在落盘前输出（与「先 delta 后写」一致）。
        print(json.dumps(payload, ensure_ascii=False))
    if args.write and merged is not None:
        try:
            written = dump_sums(merged, GOLDEN_SUMS)
        except OSError as exc:
            print(f"[freeze-golden] 写盘失败：{exc}", file=sys.stderr)
            return 2
        if not args.json_out:
            print(f"[freeze-golden] wrote={written} → {GOLDEN_SUMS}")
        return 0
    return 0 if ok else 1


def _ledger_rel() -> str:
    """台账路径的展示形式（仓库内用相对路径；被重定向到仓库外时退回绝对路径）。"""
    try:
        return REGRESSION_LEDGER.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return REGRESSION_LEDGER.as_posix()


def cmd_regression_run(args) -> int:
    """`freeze-golden` 的 dry-run 比对 + 追加一条台账记录（不写基线）。"""
    from datetime import datetime, timezone

    try:
        cases = _select_cases(_load_golden_cases(), args.case)
        all_sums = _load_golden_sums()
        results = [_freeze_one(case, all_sums, baseline=True) for case in cases]
    except (GoldenConfigError, OSError) as exc:
        print(f"[regression] 配置错误：{exc}", file=sys.stderr)
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger_rel = _ledger_rel()
    try:
        REGRESSION_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(REGRESSION_LEDGER, "a", encoding="utf-8", newline="\n") as fh:
            for item in results:
                record = {"ts": ts, "case": item["case"], "ok": item["ok"],
                          "matched": item["matched"],
                          "missing": len(item["missing"]),
                          "extra": len(item["extra"]),
                          "modified": len(item["modified"])}
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[regression] 台账写入失败：{exc}", file=sys.stderr)
        return 2

    ok = all(item["ok"] for item in results)
    if args.json_out:
        payload = {"ok": ok, "ledger": ledger_rel,
                   "cases": [{k: v for k, v in item.items() if not k.startswith("_")}
                             for item in results]}
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for item in results:
            print(f"[regression] {item['case']} {item['matched']}/{item['n_expected']} "
                  f"{_verdict(item)} → ledger={ledger_rel}")
    return 0 if ok else 1


def cmd_regression_stats(args) -> int:
    """读台账 → `summarize_runs` → **N<10 只报 k/N，不报比率**。空台账不是错误（exit 0）。"""
    records: list[dict] = []
    if REGRESSION_LEDGER.exists():
        try:
            for lineno, line in enumerate(
                    REGRESSION_LEDGER.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"第 {lineno} 行不是 JSON 对象")
                records.append(row)
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            print(f"[regression] 台账解析失败（{REGRESSION_LEDGER}）：{exc}", file=sys.stderr)
            return 2

    summary = summarize_runs(records)
    ledger_rel = _ledger_rel()
    if args.json_out:
        print(json.dumps({**summary, "ledger": ledger_rel,
                          "ledger_found": REGRESSION_LEDGER.exists()},
                         ensure_ascii=False))
        return 0
    if not REGRESSION_LEDGER.exists():
        print(f"[regression] ledger not found（{ledger_rel}）→ n=0")
    if summary["rate_suppressed"]:
        print(f"[regression] {summary['k_of_n']}（{summary['suppress_reason']}）")
    else:
        print(f"[regression] {summary['k_of_n']} rate={summary['rate']}")
    if summary["failed"]:
        print(f"[regression] failed={summary['failed']}")
    return 0


# ---- B5 后半：NFR-1 性能基线（task-008 §3.1）----

LATENCY_METRIC = "llm_latency"
MEMORY_METRIC = "peak_rss_mb"
TOKEN_METRIC = "llm_eval_count"


def _nearest_rank(sorted_values: list[float], p: int) -> float:
    """nearest-rank 百分位：下标 `ceil(p/100*n)-1`，越界取末位。**不插值**。"""
    n = len(sorted_values)
    idx = (p * n + 99) // 100 - 1
    return sorted_values[max(0, min(idx, n - 1))]


def _load_metric_rows(db_path: str | Path) -> list[tuple] | None:
    """只读打开库并取 `metric_obs` 全表；库不存在或缺表 → `None`（不建库）。"""
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return conn.execute(
                "SELECT metric, model_tag, value, run_id, ts FROM metric_obs").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def cmd_report_baseline(args) -> int:
    """NFR-1 基线：时延按 `model_tag` 分组报 nearest-rank 百分位。

    内存峰值与 tokens/s **只报可报性**：前者 0 样本，后者 `(run_id, ts)` 交集为 0
    无法逐样本配对——两者都**不输出数值**，也不拿不同样本集相除。
    库不存在或缺 `metric_obs` 表 → exit 2，**绝不**输出全 0 的「基线」。
    """
    rows = _load_metric_rows(args.db)
    if rows is None:
        print(f"[report] 库/表不可用：{args.db}（需存在且含 metric_obs 表）", file=sys.stderr)
        return 2

    latency: dict[str, list[float]] = {}
    token_samples = 0
    token_total = 0.0
    latency_total = 0.0
    peak_samples = 0
    latency_keys: set[tuple] = set()
    token_keys: set[tuple] = set()
    for metric, model_tag, value, run_id, ts in rows:
        if metric == LATENCY_METRIC:
            latency.setdefault(model_tag, []).append(float(value))
            latency_total += float(value)
            latency_keys.add((run_id, ts))
        elif metric == TOKEN_METRIC:
            token_samples += 1
            token_total += float(value)
            token_keys.add((run_id, ts))
        elif metric == MEMORY_METRIC:
            peak_samples += 1

    paired = len(latency_keys & token_keys)
    latency_n = sum(len(v) for v in latency.values())
    groups = []
    for tag in sorted(latency):
        values = sorted(latency[tag])
        groups.append({
            "model_tag": tag,
            "n": len(values),
            "p50": round(_nearest_rank(values, 50), 1),
            "p95": round(_nearest_rank(values, 95), 1),
            "mean": round(sum(values) / len(values), 1),
        })

    memory_reason = f"{MEMORY_METRIC} 样本数 {peak_samples}，未采集"
    tokens_reason = (f"{LATENCY_METRIC} 与 {TOKEN_METRIC} 的 (run_id,ts) 交集={paired}，"
                     f"无法逐样本配对")
    payload = {
        "db": str(args.db),
        "latency": groups,
        "memory": {"available": peak_samples > 0, "reason": memory_reason},
        "tokens_per_sec": {"available": False, "reason": tokens_reason},
        "totals": {
            "latency_samples": latency_n,
            "latency_total_ms": round(latency_total, 1),
            "token_samples": token_samples,
            "token_total": round(token_total, 1),
            "paired_samples": paired,
        },
    }
    if args.json_out:
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(f"[report] 时延（{LATENCY_METRIC}，单位 ms，按 model_tag 分组；nearest-rank 百分位）")
    for g in groups:
        print(f"  {g['model_tag']:<22} n={g['n']:<5} p50={g['p50']:<9} "
              f"p95={g['p95']:<9} mean={g['mean']}")
    print(f"[report] 内存峰值（{MEMORY_METRIC}）：未采集（{peak_samples} 样本）→ 不可报")
    print(f"[report] tokens/s：不可计算（{LATENCY_METRIC} 与 {TOKEN_METRIC} 的 "
          f"(run_id,ts) 交集={paired}）")
    print(f"[report] token 总量：{payload['totals']['token_total']} / 延迟总量："
          f"{payload['totals']['latency_total_ms']} ms"
          f"（样本集不同：{token_samples} vs {latency_n}，不可相除）")
    return 0


def _git_head(root: Path) -> str:
    """取仓库 HEAD（短哈希）；git 不可用或非仓库 → `unknown`（报告里只做留痕，不判）。"""
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(root),
                              capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return proc.stdout.strip() or "unknown"


# ---------------------------------------------------------------- `report acceptance`（§12.2）
# 5 项检查全部秒级判定，**不跑 pytest**（跑测试属于总闸，不属于自证命令）。

def _acceptance_a1(root: Path) -> tuple[bool, str]:
    """A1 依赖清单：`requirements.txt` 存在且非空。"""
    req = root / "requirements.txt"
    if not req.is_file():
        return False, "requirements.txt 不存在"
    body = req.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        return False, "requirements.txt 存在但为空"
    return True, f"requirements.txt 存在（{len(body.splitlines())} 行非空内容）"


def _acceptance_a2(root: Path) -> tuple[bool, str]:
    """A2 pytest 收集范围：`pytest.ini` 存在且 `testpaths` 含 `tests`。"""
    ini = root / "pytest.ini"
    if not ini.is_file():
        return False, "pytest.ini 不存在"
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if text.startswith("testpaths") and "tests" in text:
            return True, f"pytest.ini 存在，{text}"
    return False, "pytest.ini 缺少指向 tests 的 testpaths"


def _acceptance_a3(root: Path) -> tuple[bool, str]:
    """A3 数据库 schema 完整：`schema.sql` 声明的**每一张表**都在库中。

    **必须 `mode=ro` 只读打开**，不许走 `store/db.py::connect()`——它带 `ensure_schema()`
    会自动建库，等于把「库不存在」洗成 PASS（本项目历史上抓过的假绿模式）。
    """
    db = root / "data" / "mm.db"
    if not db.is_file():
        return False, f"data/mm.db 不存在：{db}"
    schema = root / "mmagent" / "store" / "schema.sql"
    try:
        from .store.db import schema_objects

        declared = [name for kind, name, _ in
                    schema_objects(schema.read_text(encoding="utf-8")) if kind == "table"]
    except (OSError, ImportError) as exc:
        return False, f"读取 schema.sql 失败：{exc}"
    try:
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return False, f"只读打开失败：{exc}"
    try:
        present = {row[0] for row in
                   conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as exc:
        return False, f"查询 sqlite_master 失败：{exc}"
    finally:
        conn.close()
    absent = [t for t in declared if t not in present]
    if absent:
        return False, f"缺表 {len(absent)}/{len(declared)}：{', '.join(absent)}"
    return True, f"{len(declared)} 张表齐备（sqlite_master 实查）"


def _acceptance_a4(root: Path) -> tuple[bool, str]:
    """A4 golden 基线：`tests/golden/SHA256SUMS` 存在且行数 > 0。"""
    sums = root / "tests" / "golden" / "SHA256SUMS"
    if not sums.is_file():
        return False, "tests/golden/SHA256SUMS 不存在"
    n = len([ln for ln in sums.read_text(encoding="utf-8", errors="replace").splitlines()
             if ln.strip()])
    if n <= 0:
        return False, "tests/golden/SHA256SUMS 为空"
    return True, f"SHA256SUMS 存在（{n} 行）"


def _acceptance_a5(root: Path) -> tuple[bool, str]:
    """A5 前端演示页：`frontend/index.html` 存在。"""
    page = root / "frontend" / "index.html"
    if not page.is_file():
        return False, "frontend/index.html 不存在"
    return True, f"frontend/index.html 存在（{page.stat().st_size} 字节）"


def acceptance_checks(root: Path) -> list[tuple[str, str, bool, str]]:
    """跑 5 项验收检查 → `[(编号, 名称, 是否 PASS, 说明)]`（只读，不写盘）。"""
    return [
        ("A1", "依赖清单", *_acceptance_a1(root)),
        ("A2", "pytest 收集范围", *_acceptance_a2(root)),
        ("A3", "数据库 schema 完整", *_acceptance_a3(root)),
        ("A4", "golden 基线", *_acceptance_a4(root)),
        ("A5", "前端演示页", *_acceptance_a5(root)),
    ]


def cmd_report_acceptance(args) -> int:
    """`report acceptance`：仓库验收自证（5 项），写 `artifacts/reports/acceptance.md`。

    退出码：5 项全 PASS → 0；任一 FAIL → 打印不符项后 1（**不抛异常**）。
    """
    root = REPO_ROOT
    results = acceptance_checks(root)
    passed = sum(1 for *_, ok, _ in results if ok)

    print("| 检查 | 结果 | 说明 |")
    print("|---|---|---|")
    for code, name, ok, detail in results:
        print(f"| {code} {name} | {'PASS' if ok else 'FAIL'} | {detail} |")

    head = _git_head(root)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    out = Path(getattr(args, "out", "") or "artifacts/reports/acceptance.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 仓库验收自证（`python -m mmagent.cli report acceptance`）",
        "",
        f"- 时间戳：{stamp}",
        f"- HEAD：{head}",
        f"- 结论：{passed}/{len(results)} PASS",
        "",
        "| 检查 | 结果 | 说明 |",
        "|---|---|---|",
        *[f"| {code} {name} | {'PASS' if ok else 'FAIL'} | {detail} |"
          for code, name, ok, detail in results],
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"[report] 验收报告已写入：{out}（HEAD={head}）")

    failed = [(code, name, detail) for code, name, ok, detail in results if not ok]
    if failed:
        print(f"[report] 不符项 {len(failed)}/{len(results)}：", file=sys.stderr)
        for code, name, detail in failed:
            print(f"  {code} {name}：{detail}", file=sys.stderr)
        return 1
    print(f"[report] {passed}/{len(results)} PASS")
    return 0


# ---------------------------------------------------------------- `net-audit`（§12.3）

def cmd_net_audit(args) -> int:
    """`net-audit --since <ts> --out <path>`：出网台账检索（薄包 `store/audit.list_net_audit`）。

    `--since` 过滤在 **CLI 层**做（`list_net_audit` 不支持 since，本命令不改 `store/audit.py`）：
    取 `--limit` 条后按 `ts >= since` 字符串比较——`ts` 是 ISO8601 UTC 定长格式，字典序即时间序。
    退出码：0=正常（含空结果）；2=库不存在 / `--since` 非法 / 查询失败。
    """
    from .store.audit import list_net_audit

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"库不存在：{db_path}", file=sys.stderr)
        return 2
    since = (getattr(args, "since", "") or "").strip()
    if since:
        try:
            datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            print(f"--since 不是合法 ISO8601：{since!r}", file=sys.stderr)
            return 2
    try:
        rows = list_net_audit(limit=max(0, int(args.limit)), db_path=db_path)["rows"]
    except sqlite3.Error as exc:
        print(f"[net-audit] 查询失败：{exc}", file=sys.stderr)
        return 2
    if since:
        rows = [r for r in rows if str(r.get("ts") or "") >= since]

    payload = {"since": since or None, "count": len(rows), "rows": rows}
    if getattr(args, "out", ""):
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8", newline="\n")
        print(f"[net-audit] 已写入：{out}"
              f"（since={since or '—'} count={len(rows)}）")
        return 0
    if getattr(args, "json_out", False):
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(f"[net-audit] since={since or '—'} count={len(rows)}")
    print(f"{'ts':<26} {'provider':<12} {'role':<10} {'ok':<3} status")
    for row in rows:
        print(f"{str(row.get('ts') or ''):<26} {str(row.get('provider') or ''):<12} "
              f"{str(row.get('role') or ''):<10} {int(row.get('ok') or 0):<3} "
              f"{row.get('error_code') or ''}")
    return 0


# ---------------------------------------------------------------- `ollama verify`（§12.1 步骤 3）

def _norm_model_tag(name: str) -> str:
    """`qwen3.5` 与 `qwen3.5:latest` 视为同一模型（Ollama 的默认 tag 语义）。"""
    tag = name.strip()
    return tag if ":" in tag else f"{tag}:latest"


def cmd_ollama_verify(args) -> int:
    """`ollama verify --models a,b,c`：校验所需模型是否已就绪。

    退出码（裁决）：**不可达 → 2**（本机 Ollama 未启属正常状态，不是验收失败）；
    可达但缺模型 → 1；齐备 → 0。
    """
    wanted = [m.strip() for m in (args.models or "").split(",") if m.strip()]
    if not wanted:
        print("[ollama] --models 为空（逗号分隔的模型名）", file=sys.stderr)
        return 2
    info = _ollama_models()
    if not info.get("available"):
        print("[ollama] Ollama 不可达（localhost:11434），无法校验模型", file=sys.stderr)
        return 2
    installed = [str(m) for m in (info.get("models") or [])]
    have = {_norm_model_tag(m) for m in installed}
    missing = [m for m in wanted if _norm_model_tag(m) not in have]
    ready = [m for m in wanted if _norm_model_tag(m) in have]

    if not missing:
        print(f"[ollama] {len(wanted)}/{len(wanted)} 模型已就绪")
        for name in wanted:
            print(f"  已就绪：{name}")
        return 0
    print(f"[ollama] 缺 {len(missing)}/{len(wanted)} 模型", file=sys.stderr)
    for name in missing:
        print(f"  缺：{name}", file=sys.stderr)
    for name in ready:
        print(f"  已就绪：{name}", file=sys.stderr)
    print(f"[ollama] 本机已装：{', '.join(installed) or '（无）'}", file=sys.stderr)
    return 1


def cmd_report(args) -> int:
    """`report` 入口：`acceptance` → 仓库验收自证；否则走 `--baseline`。"""
    if getattr(args, "action", None) == "acceptance":
        return cmd_report_acceptance(args)
    if not args.baseline:
        print("[report] 目前仅支持 --baseline", file=sys.stderr)
        return 2
    return cmd_report_baseline(args)


def cmd_rag_rebuild(args) -> int:
    """`cli rag rebuild`：调 embedding 接口重建 `artifacts/rag/index/`（FR-4「可重建」入口）。

    退出码：0 成功；2 语料/配置不可用（清单缺失、sha256 不符、密钥缺失、embedding 失败）。
    **一次构建内 embedding 调用是串行的**（按批 8 条），不与其它采样并发。
    """
    from .rag import adapter as rag_adapter
    from .tools import rag_index_tool as idx_tool

    start = time.monotonic()
    try:
        manifest = rag_adapter.rebuild(
            args.corpus, args.out,
            chunk_chars=args.chunk_chars, chunk_overlap=args.overlap)
    except idx_tool.CorpusMismatchError as exc:
        print(f"[rag rebuild] {exc}", file=sys.stderr)
        return 2
    except (idx_tool.RagIndexError, FileNotFoundError) as exc:
        print(f"[rag rebuild] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  密钥缺失/网络失败统一给可读诊断
        print(f"[rag rebuild] 取向量失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    elapsed_s = round(time.monotonic() - start, 1)

    if args.json_out:
        print(json.dumps(manifest, ensure_ascii=False))
        return 0

    out_dir = Path(args.out)
    print(f"[rag rebuild] ok chunks={manifest['chunks']} dims={manifest['dims']} "
          f"model={manifest['model']} corpus={len(manifest['corpus'])} "
          f"chunk_chars={manifest['chunk_chars']} overlap={manifest['chunk_overlap']} "
          f"elapsed={elapsed_s}s")
    print(f"  built_at={manifest['built_at']}")
    print(f"  manifest={idx_tool.display_path(out_dir / idx_tool.MANIFEST_NAME)}")
    print(f"  vectors={idx_tool.display_path(out_dir / idx_tool.VECTORS_NAME)}")
    for doc in manifest["corpus"]:
        print(f"  {doc['name']}: chars={doc['chars']} chunks={doc['chunks']} "
              f"sha256={doc['sha256'][:12]}…")
    return 0


def cmd_rag_search(args) -> int:
    """`cli rag search "<问句>"`：打印 rank/score/source（+ 片段首行）。

    退出码：0 成功（**含 0 命中**）；2 索引缺失 / 取向量失败。
    """
    from .rag import adapter as rag_adapter

    try:
        result = rag_adapter.search(args.query, args.k, index_root=args.index)
    except rag_adapter.IndexMissingError as exc:
        print(f"[rag search] {exc}", file=sys.stderr)
        return 2
    except rag_adapter.RagEmbedFailed as exc:
        print(f"[rag search] 取向量失败：{exc}", file=sys.stderr)
        return 2

    if args.json_out:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    meta = result["index"]
    print(f"[rag search] query={result['query']!r} k={result['k']} hits={len(result['hits'])} "
          f"index_chunks={meta['chunks']} dims={meta['dims']} model={meta['model']}")
    for hit in result["hits"]:
        head = hit["text"].strip().splitlines()[0] if hit["text"].strip() else ""
        print(f"  #{hit['rank']} score={hit['score']:.4f} source={hit['source']} "
              f"[{hit['start']},{hit['end']}) {head[:40]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器。"""
    parser = argparse.ArgumentParser(prog="mmagent", description="数模协作 Agent CLI")
    # 规划书 §9.5：`--version` 在**参数解析阶段**直接 SystemExit(0)，不受 required subparsers 约束
    parser.add_argument("--version", action="version", version=f"mmagent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_db = sub.add_parser("db", help="数据库操作")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    p_db_init = db_sub.add_parser("init", help="初始化数据库")
    p_db_init.add_argument("--db", default="data/mm.db", help="数据库路径")
    p_db_init.set_defaults(func=cmd_db_init)

    p_intake = sub.add_parser("intake", help="附件体检")
    p_intake.add_argument("--dataset", required=True, help="数据集目录")
    p_intake.add_argument("--out", required=True, help="产物输出目录")
    p_intake.set_defaults(func=cmd_intake)

    p_env = sub.add_parser("env-attest", help="环境留存 / 校验")
    env_grp = p_env.add_mutually_exclusive_group(required=True)
    env_grp.add_argument("--out", default=None, help="留存 JSON 路径")
    env_grp.add_argument("--verify", default=None, metavar="PATH",
                         help="校验一份已有留存（只判 python_version / python_implementation / "
                              "dependencies / requirements_sha256_16）")
    p_env.set_defaults(func=cmd_env_attest)

    p_net_audit = sub.add_parser(
        "net-audit", help="出网台账检索（net_audit 表，规划书 §12.3）",
        description=("薄包 `store/audit.list_net_audit`：按 ts 降序取 --limit 条，"
                     "`--since` 过滤在 CLI 层做（ts 为定长 ISO8601 UTC，字典序即时间序）。"
                     "退出码：0=正常（含空结果）；2=库不存在 / --since 非法 / 查询失败。"))
    p_net_audit.add_argument("--since", default="",
                             help="起始时间（含），ISO8601，如 2026-01-01T00:00:00Z")
    p_net_audit.add_argument("--out", default="", help="JSON 落盘路径（可选）")
    p_net_audit.add_argument("--db", default="data/mm.db", help="台账库")
    p_net_audit.add_argument("--limit", type=int, default=1000, help="最多取多少条（默认 1000）")
    p_net_audit.add_argument("--json", action="store_true", dest="json_out",
                             help="stdout 只输出一个 JSON 对象")
    p_net_audit.set_defaults(func=cmd_net_audit)

    p_ollama = sub.add_parser("ollama", help="本地 Ollama 环境校验")
    ollama_sub = p_ollama.add_subparsers(dest="ollama_command", required=True)
    p_ollama_verify = ollama_sub.add_parser(
        "verify", help="校验所需模型是否已就绪（规划书 §12.1 步骤 3）",
        description=("退出码：0=模型齐备；1=可达但缺模型；**2=Ollama 不可达**"
                     "（本机 Ollama 未启属正常状态，不判成验收失败）。"))
    p_ollama_verify.add_argument("--models", required=True,
                                 help="逗号分隔的模型名，如 qwen3.5,qwen3:4b")
    p_ollama_verify.set_defaults(func=cmd_ollama_verify)

    p_net = sub.add_parser("netguard-check", help="名单制出网检查（白名单外 host 判违规）")
    p_net.set_defaults(func=cmd_netguard_check)

    p_des = sub.add_parser("desensitize-check", help="NFR-7 脱敏扫描")
    p_des.add_argument("--input", required=True, help="待扫描文本文件（逐行扫描）")
    p_des.add_argument("--field", default="", help="列名（可选，用于列级规则）")
    p_des.add_argument("--table", default="", help="表 id（可选）")
    p_des.add_argument("--mask-out", default="", help="脱敏副本输出路径（可选）")
    p_des.add_argument("--db", default="data/mm.db", help="命中台账数据库路径")
    p_des.set_defaults(func=cmd_desensitize_check)

    p_solve = sub.add_parser(
        "solve", help="FR-5 求解：生成→守卫→执行→（可选）修复（P0-2）",
        description=("FR-5 求解入口。退出码：0=成功（run.ok=True 且 DoD 达标，"
                     "即 results/summary.csv + dpi≈300 的 PNG 齐备）；"
                     "2=输入/spec 非法（spec 缺失或 JSON 非法、输入文件缺失）；"
                     "5=求解失败（生成/守卫/执行失败，含「未启用修复轮时首轮失败」）"
                     "或 DoD 不达标（dod.ok=False，如缺 results/summary.csv）。"))
    p_solve.add_argument("--spec", required=True, help="任务规格 JSON 路径")
    p_solve.add_argument("--workdir", required=True, help="工作目录（脚本产物落此）")
    p_solve.add_argument("--max-attempts", type=int, default=2,
                         help=("生成+修复的总尝试数（默认 2 = 生成 1 次 + 修复 1 轮）；"
                               "1 表示**只生成一次、不修复**（repair_rounds=max(0,N-1)）；"
                               "首轮失败则退出码 5"))
    p_solve.add_argument("--schema-report", default="",
                         help="FR-2 schema_report.json 路径（缺省用内置默认）")
    p_solve.add_argument("--no-auto-schema", action="store_true",
                         help="关闭 data_schema 自动装配")
    p_solve.add_argument("--timeout-s", type=int, default=300, help="脚本执行超时（秒）")
    p_solve.set_defaults(func=cmd_solve)

    p_run = sub.add_parser("run", help="人机回环运行（LangGraph interrupt + SqliteSaver）")
    run_sub = p_run.add_subparsers(dest="run_command", required=True)

    p_run_start = run_sub.add_parser("start", help="启动一次运行，遇卡点即挂起（exit 0）")
    p_run_start.add_argument("--run-id", required=True, help="运行 id（= langgraph thread_id）")
    p_run_start.add_argument("--conflicts", action="store_true", help="注入口径冲突（触发卡点1）")
    p_run_start.add_argument("--conflict-detail", default="",
                             help="口径冲突待审材料（JSON，可选；缺省用内置示例）")
    p_run_start.add_argument("--sanity-blocking", action="store_true",
                             help="注入 sanity 阻断（触发卡点2）")
    p_run_start.add_argument("--sanity-detail", default="",
                             help="sanity 阻断待审材料（JSON，可选）")
    p_run_start.add_argument("--ckpt", default="data/checkpoints.db", help="检查点库路径")
    p_run_start.add_argument("--db", default="data/mm.db", help="事件台账库路径")
    p_run_start.set_defaults(func=cmd_run_start)

    p_run_resume = run_sub.add_parser("resume", help="投递人工裁定，从挂起点恢复")
    p_run_resume.add_argument("--run-id", required=True, help="运行 id（= langgraph thread_id）")
    p_run_resume.add_argument("--gate", required=True, help="卡点名（须与当前挂起一致）")
    p_run_resume.add_argument("--decision", required=True, help="JSON 字符串裁定")
    p_run_resume.add_argument("--ckpt", default="data/checkpoints.db", help="检查点库路径")
    p_run_resume.add_argument("--db", default="data/mm.db", help="事件台账库路径")
    p_run_resume.set_defaults(func=cmd_run_resume)

    p_run_status = run_sub.add_parser("status", help="查看当前阶段与是否挂起")
    p_run_status.add_argument("--run-id", required=True, help="运行 id（= langgraph thread_id）")
    p_run_status.add_argument("--ckpt", default="data/checkpoints.db", help="检查点库路径")
    p_run_status.add_argument("--db", default="data/mm.db", help="事件台账库路径")
    p_run_status.set_defaults(func=cmd_run_status)

    p_serve = sub.add_parser(
        "serve", help="启动本地 HTTP 服务（API + 静态前端；默认仅回环）",
        description=("启动本地 HTTP 服务：挂载 /api/v1 与静态前端 /。"
                     "默认仅监听回环地址；--host 指定非回环地址时须显式加 --allow-lan，"
                     "否则打印拒因并以 exit 2 退出。frontend/ 不存在且未给 --no-static "
                     "亦 exit 2。"))
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    p_serve.add_argument("--port", type=int, default=8000, help="监听端口")
    p_serve.add_argument("--db", default="data/mm.db", help="默认事件台账库路径")
    p_serve.add_argument("--ckpt", default="data/checkpoints.db", help="默认检查点库路径")
    p_serve.add_argument("--allow-lan", action="store_true",
                         help="允许监听非回环地址（默认拒绝；启用后服务对外可见）")
    p_serve.add_argument("--no-static", action="store_true",
                         help="不挂载静态前端（frontend/ 缺失时仅启动 API）")
    p_serve.set_defaults(func=cmd_serve)

    # ---- P0 验收命令（A2，规划书 §4.2）----
    p_replot = sub.add_parser(
        "re-plot", help="FR-6 重出图：结果表 + figure specs → PNG(dpi300)（P0-3）")
    p_replot.add_argument("--result-table", action="append", required=True,
                          help="结果表 CSV，可多次；写成 name=path 取 name 为表 id，否则取文件名 stem")
    p_replot.add_argument("--manifest", required=True,
                          help="figure specs 文件（.yaml/.yml/.json）")
    p_replot.add_argument("--out", default="figures", help="出图目录（默认 figures）")
    p_replot.add_argument("--timeout-s", type=int, default=300, dest="timeout_s",
                          help="耗时阈值（规划书 §4.2.3：≤300s；超过判 exit 5）")
    p_replot.set_defaults(func=cmd_re_plot)

    p_profile = sub.add_parser("profile", help="赛题档案操作（FR-9）")
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    p_profile_show = profile_sub.add_parser("show", help="读取档案版本（P0-4）")
    p_profile_show.add_argument("--profile", required=True, help="档案 id")
    p_profile_show.add_argument("--version", default="last", help="last 或整数 version_id")
    p_profile_show.add_argument("--json", default="",
                                help="落盘路径；'-' 表示只走 stdout")
    p_profile_show.add_argument("--db", default="data/mm.db", help="档案库")
    p_profile_show.set_defaults(func=cmd_profile_show)

    p_profile_trace = profile_sub.add_parser(
        "trace", help="字段级溯源：producer/produced_at/version_id/upstream_refs（A4）")
    p_profile_trace.add_argument("--profile", required=True, help="档案 id")
    p_profile_trace.add_argument("--version", default="last", help="last 或整数 version_id")
    p_profile_trace.add_argument("--field", default="", help="精确匹配 field_path（可选）")
    p_profile_trace.add_argument("--db", default="data/mm.db", help="档案库")
    p_profile_trace.add_argument("--limit", type=int, default=1000, help="最多返回行数")
    p_profile_trace.set_defaults(func=cmd_profile_trace)

    p_problem = sub.add_parser("problem", help="赛题题面解析（FR-1）")
    problem_sub = p_problem.add_subparsers(dest="problem_command", required=True)
    p_problem_parse = problem_sub.add_parser(
        "parse", help="题面 → 结构化子问题（三要素 + source_quote），写入 draft 档案",
        description=("只从题面原文抽取、不补写。source_quote 未命中原文的子问题会降级进 "
                     "open_questions；given/constraints 里原文没有的数字会被剔除该分句。"
                     "退出码：0=成功（写入 draft 版本）；2=输入非法（文件不存在/题面为空）；"
                     "5=LLM 调用失败（含重试与降级）。"))
    p_problem_parse.add_argument("--text", required=True,
                                 help="题面文本路径；'-' 表示从 stdin 读")
    p_problem_parse.add_argument("--profile-id", required=True, help="档案 id")
    p_problem_parse.add_argument("--actor", default="archivist", help="写入档案的操作者")
    p_problem_parse.add_argument("--db", default="data/mm.db", help="档案/台账库")
    p_problem_parse.add_argument("--json", action="store_true", dest="json_out",
                                 help="打印完整解析结果（含 subproblems/open_questions）")
    p_problem_parse.set_defaults(func=cmd_problem_parse)

    p_plan = sub.add_parser("plan", help="建模方案卡片（FR-3）")
    plan_sub = p_plan.add_subparsers(dest="plan_command", required=True)
    p_plan_build = plan_sub.add_parser(
        "build", help="题面 → ≥3 个候选方法论 + 反造假校验，落 artifacts/plan/<run_id>.json",
        description=("产出「可执行候选方法」交人裁，不给结论：候选 <3 条、字段缺项、方法重名、"
                     "方法名为占位词或带评价词（推荐/首选）一律判输出无效。反造假按分句剔除"
                     "题面里没出现过的数字。结果落 artifacts/plan/<run_id>.json 并登记 "
                     "artifacts(kind=plan)，不自动流入 solve。退出码：0=成功（含 valid=false，"
                     "需人工核对）；2=输入非法；3=写盘失败；5=LLM 调用失败或输出过不了严格闸。"))
    p_plan_build.add_argument("--text", required=True,
                              help="题面文本路径；'-' 表示从 stdin 读")
    p_plan_build.add_argument("--profile-id", default="",
                              help="关联档案 id（可为空；只用于登记与追溯）")
    p_plan_build.add_argument("--out-dir", default="",
                              help="产物目录（缺省 artifacts/plan/）")
    p_plan_build.add_argument("--run-id", default="",
                              help="产物文件名与登记 run_id（缺省自动生成）")
    p_plan_build.add_argument("--db", default="data/mm.db", help="档案/台账库")
    p_plan_build.add_argument("--json", action="store_true", dest="json_out",
                              help="打印完整 plan JSON（含 tasks/open_questions）")
    p_plan_build.set_defaults(func=cmd_plan_build)

    p_paper = sub.add_parser("paper", help="论文装配（FR-7）")
    paper_sub = p_paper.add_subparsers(dest="paper_command", required=True)
    p_paper_build = paper_sub.add_parser(
        "build", help="档案 → 五章 docx 骨架 + sidecar manifest（确定性，不调 LLM）",
        description=("按固定骨架装配 docx：摘要/一~五章/参考文献/附录 A。**只排结构**，"
                     "不撰写摘要与论证段落、不编造公式/符号/文献/图；每段用 "
                     "mm-origin-agent|human|placeholder 样式标来源。退出码：0=成功；"
                     "2=档案或版本不存在；3=写盘失败。"))
    p_paper_build.add_argument("--profile-id", required=True, help="档案 id")
    p_paper_build.add_argument("--version", default="last", help="last 或整数 version_id")
    p_paper_build.add_argument("--figures-dir", default="",
                               help="图题来源目录（缺省 figures/；没有图就没有图题）")
    p_paper_build.add_argument("--results-dir", default="",
                               help="附录代码来源目录（缺省 results/，读 solve.py）")
    p_paper_build.add_argument("--out", required=True, help="docx 输出路径")
    p_paper_build.add_argument("--actor", default="assembler", help="装配者标识（仅记账）")
    p_paper_build.add_argument("--db", default="data/mm.db", help="档案库")
    p_paper_build.add_argument("--json", action="store_true", dest="json_out",
                               help="打印完整返回 dict")
    p_paper_build.set_defaults(func=cmd_paper_build)

    p_export = sub.add_parser("export", help="导出交付（FR-12）")
    export_sub = p_export.add_subparsers(dest="export_command", required=True)
    p_export_build = export_sub.add_parser(
        "build", help="paper.docx → 可交付目录（页脚署名 + 阻断闸 + SHA256SUMS）",
        description=("把 paper.docx 与附件打成交付目录：页脚写「本文由 AI 辅助生成，已由 "
                     "XXX 人工审核」（写在副本上，源文件不动），并出 manifest.json 与 "
                     "SHA256SUMS。阻断闸：paper 缺失 / sidecar 段落数不符 / 档案 draft"
                     "（可 --allow-draft）/ open_questions 非空（可 --allow-open-questions）/ "
                     "交付文件数 <10（可 --allow-few-files）/ 缺合规声明"
                     "（可 --allow-missing-disclosure）/ 结果表越界（可 --allow-sanity）。"
                     "退出码：0=成功；2=--version 非法；3=写盘失败；4=reviewer 缺失或过短；"
                     "6=被阻断。"))
    p_export_build.add_argument("--paper", required=True, help="源 paper.docx 路径")
    p_export_build.add_argument("--out-dir", required=True, help="交付目录（不存在则创建）")
    # 注意：**不用** argparse 的 `required=True`。那会让缺参直接 `SystemExit(2)`，
    # 既走不到「缺 reviewer → exit 4」的契约，又让 `cli_main()` 抛异常而非返回 int。
    # 这里给 `default=None`，把 None/空白交给 `build_deliverable` 的校验 → ValueError → 4。
    p_export_build.add_argument("--reviewer", default=None,
                                help="人工审核人姓名（必填，去空白后 ≥2 字符；缺失或非法 → exit 4）")
    p_export_build.add_argument("--figures-dir", default="", help="图目录（可选）")
    p_export_build.add_argument("--results-dir", default="", help="结果目录（可选）")
    p_export_build.add_argument("--compliance-dir", default="", help="合规产物目录（可选）")
    p_export_build.add_argument("--profile-id", default="", help="档案 id（用于 draft/待确认闸）")
    p_export_build.add_argument("--version", default="last", help="last 或整数 version_id")
    p_export_build.add_argument("--allow-draft", action="store_true",
                                help="放行未 adopt 的 draft 档案（manifest 记 draft_override）")
    p_export_build.add_argument("--allow-open-questions", action="store_true",
                                help="放行非空 open_questions（manifest 记条数）")
    p_export_build.add_argument("--allow-few-files", action="store_true",
                                help="放行交付文件数 <10（manifest 记 file_count_override）")
    p_export_build.add_argument("--allow-missing-disclosure", action="store_true",
                                help="放行缺合规声明（manifest 记 disclosure_override）")
    p_export_build.add_argument("--allow-sanity", action="store_true",
                                help="放行结果表越界报警（manifest 记 sanity_override）")
    p_export_build.add_argument("--db", default="data/mm.db", help="档案/台账库")
    p_export_build.add_argument("--json", action="store_true", dest="json_out",
                                help="打印完整返回 dict")
    p_export_build.set_defaults(func=cmd_export_build)

    p_compliance = sub.add_parser("compliance", help="合规产物（FR-13）")
    compliance_sub = p_compliance.add_subparsers(dest="compliance_command", required=True)
    p_compliance_build = compliance_sub.add_parser(
        "build", help="生成 AI 使用披露 md+docx（P0-5）")
    p_compliance_build.add_argument("--profile", required=True, help="档案 id")
    p_compliance_build.add_argument("--version", default="last", help="last 或整数 version_id")
    p_compliance_build.add_argument("--out", default="artifacts/compliance",
                                    help="披露声明落盘目录")
    p_compliance_build.add_argument("--human-edit-ratio", type=float, default=None,
                                    help="人工核定的人改比例（缺省由工具自行核算）")
    p_compliance_build.add_argument("--db", default="data/mm.db", help="台账库")
    p_compliance_build.set_defaults(func=cmd_compliance_build)

    # ---- FR-14 档案库（历年真题 + 官方论文）----
    p_archive = sub.add_parser("archive", help="历年真题 / 官方论文档案库（FR-14）")
    archive_sub = p_archive.add_subparsers(dest="archive_command", required=True)

    p_archive_ingest = archive_sub.add_parser(
        "ingest", help="manifest → archive_item（按 archive_id 幂等，只读本地文件）",
        description=("读 artifacts/exam_corpus/manifest.json 落库；全程不联网。"
                     "退出码：0=全部落库且素材齐备（含附件）；2=manifest 不可用（缺失/JSON 坏/"
                     "schema_version 不符/字段非法/路径穿越）；"
                     "4=有 file_missing / attachment_missing / sha_mismatch"
                     "（部分落空，行已入库但素材不完整）。"))
    p_archive_ingest.add_argument("--manifest",
                                  default="artifacts/exam_corpus/manifest.json",
                                  help="素材清单 JSON 路径")
    p_archive_ingest.add_argument("--db", default="data/mm.db", help="台账库")
    p_archive_ingest.add_argument("--root", default="",
                                  help="素材根目录（缺省=仓库根）；测试/搬运语料时用")
    p_archive_ingest.add_argument("--verify-sha", action="store_true",
                                  help="重算主体与附件的 sha256 并与 manifest 比对（论文按逐页拼接口径）")
    p_archive_ingest.set_defaults(func=cmd_archive_ingest)

    p_archive_list = archive_sub.add_parser(
        "list", help="档案清单（file_present 为现算值）",
        description=("按年/类型/关键词过滤并分页；`--json` 输出完整返回体。"
                     "退出码：0=查询成功（空结果也算成功）；2=参数非法或库不可用。"))
    p_archive_list.add_argument("--year", type=int, default=None, help="年份（如 2025）")
    p_archive_list.add_argument("--kind", default="", choices=["", "exam_problem", "paper"],
                                help="类型：exam_problem（真题）或 paper（论文）")
    p_archive_list.add_argument("--q", default="", help="关键词（匹配 title / archive_id）")
    p_archive_list.add_argument("--limit", type=int, default=200, help="本页条数")
    p_archive_list.add_argument("--offset", type=int, default=0, help="偏移（默认 0）")
    p_archive_list.add_argument("--db", default="data/mm.db", help="台账库")
    p_archive_list.add_argument("--root", default="",
                                help="素材根目录（缺省=仓库根）")
    p_archive_list.add_argument("--json", action="store_true", dest="json_out",
                                help="stdout 只输出一个 JSON 对象")
    p_archive_list.set_defaults(func=cmd_archive_list)

    p_archive_summary = archive_sub.add_parser(
        "summary", help="统计（总数/按类型/按年）+ 文件缺失数 + manifest 存在性",
        description=("`file_missing` 为**现算**值（库里的 local_path 逐个查磁盘），"
                     "manifest 缺失会显式报 `manifest_present=false`。"
                     "退出码：0=统计成功；2=库不可用。"))
    p_archive_summary.add_argument("--db", default="data/mm.db", help="台账库")
    p_archive_summary.add_argument("--root", default="",
                                   help="素材根目录（缺省=仓库根）")
    p_archive_summary.add_argument("--json", action="store_true", dest="json_out",
                                   help="stdout 只输出一个 JSON 对象")
    p_archive_summary.set_defaults(func=cmd_archive_summary)

    # ---- FR-8 进度与时间管理（最小形态）----
    p_progress = sub.add_parser("progress", help="进度与时间管理（FR-8）")
    progress_sub = p_progress.add_subparsers(dest="progress_command", required=True)
    p_progress_status = progress_sub.add_parser(
        "status",
        help="倒计时 + 无产出预警 + 不做清单 + 最短可交版本（只读不写库）",
        description=("只读：以 `mode=ro` 打开台账库，按 artifacts/events 的时间戳算无产出时长，"
                     "并结合传入的 deadline 触发「前 6 h 转最短可交版本 / 前 4 h 触发不做清单」。"
                     "退出码：0=无预警；2=库不存在或参数非法（含 `--deadline`/`--now` 无法解析）；"
                     "4=有预警（非失败，仅提示）。"))
    p_progress_status.add_argument("--db", default="data/mm.db", help="台账库（只读打开）")
    p_progress_status.add_argument(
        "--deadline", default="",
        help="截止时间（ISO8601，如 2026-10-20T22:00:00Z）；缺省则时间类规则一律不触发")
    p_progress_status.add_argument(
        "--now", default="",
        help="注入当前时间（ISO8601，UTC 解释）。缺省取真实时钟；**仅供复现/演示，勿用于伪造进度**")
    p_progress_status.add_argument("--profile-id", default="", help="只统计该档案的产物与事件")
    p_progress_status.add_argument("--json", action="store_true", dest="json_out",
                                   help="stdout 只输出一个 JSON 对象")
    p_progress_status.set_defaults(func=cmd_progress_status)

    # ---- FR-10 一致性校验（正文数字 vs 结果文件）----
    p_consistency = sub.add_parser("consistency", help="正文数字 vs 结果文件（FR-10）")
    consistency_sub = p_consistency.add_subparsers(dest="consistency_command", required=True)
    p_consistency_check = consistency_sub.add_parser(
        "check",
        help="校验论文正文数字能否在结果表里找到出处（只读）",
        description=("只读：抽 paper.docx 正文段落的数字 token，与 results_dir/**/*.csv 的数字集合求差。"
                     "有 sidecar 时按段级来源判定（只校验人工撰写段），无 sidecar 时退回启发式。"
                     "退出码：0=ok（全命中）；2=输入不可用；4=mismatch（有数字无出处，阻断）；"
                     "6=no_data/stale_sidecar（**没校验成，不等于通过**）。"))
    p_consistency_check.add_argument("--paper", required=True, help="待校验的 paper.docx")
    p_consistency_check.add_argument("--results-dir", required=True,
                                     help="结果目录（递归读 **/*.csv）")
    p_consistency_check.add_argument("--sidecar", default="",
                                     help="manifest 路径（缺省自动找 docx 同名 *.manifest.json）")
    p_consistency_check.add_argument("--include-appendix", action="store_true",
                                     help="把附录 A 代码段也纳入校验（默认跳过）")
    p_consistency_check.add_argument("--ignore-token", action="append", default=None,
                                     metavar="TOKEN",
                                     help="显式豁免的数字（可多次；命中的会回显在 ignored 里）")
    p_consistency_check.add_argument("--json", action="store_true", dest="json_out",
                                     help="stdout 只输出一个 JSON 对象")
    p_consistency_check.set_defaults(func=cmd_consistency_check)

    # ---- NFR-4 冻结基线（B1，task-003）----
    p_freeze = sub.add_parser(
        "freeze-golden",
        help="重跑产线并与 NFR-4 冻结基线比对（默认 dry-run 不写盘）",
        description=("按 tests/golden/cases.json 重跑产线到临时目录，与 tests/golden/SHA256SUMS "
                     "比对。退出码：0=delta 为 0（或 --write 成功）；1=delta≠0 且未 --write；"
                     "2=配置错误（cases.json/基线/数据集不可用）。--write 会先打印漂移明细再写，"
                     "报文含 OVERWRITE。"))
    p_freeze.add_argument("--case", default="", help="只跑指定 case id（缺省跑全部）")
    p_freeze.add_argument("--write", action="store_true",
                          help="把本次产物哈希写入 tests/golden/SHA256SUMS（默认绝不写盘）")
    p_freeze.add_argument("--json", action="store_true", dest="json_out",
                          help="stdout 只输出一个 JSON 对象")
    p_freeze.set_defaults(func=cmd_freeze_golden)

    p_spot = sub.add_parser(
        "spotcheck",
        help="只读比对当前树 vs NFR-4 冻结基线",
        description=("只读：比对 tests/golden/SHA256SUMS 与当前工作树（不跑产线、不写盘）。"
                     "目录内多出文件同样判 FAIL（内容比对含 extra）。退出码："
                     "0=全部 PASS；1=任一 FAIL；2=配置错误（基线缺失/非法）。"))
    p_spot.add_argument("--case", default="", help="只跑指定 case id（缺省跑全部）")
    p_spot.add_argument("--root", default="",
                        help="比对基准目录（缺省=仓库根）；用于在临时副本上做变异验证")
    p_spot.add_argument("--json", action="store_true", dest="json_out",
                        help="stdout 只输出一个 JSON 对象")
    p_spot.set_defaults(func=cmd_spotcheck)

    p_reg = sub.add_parser("regression", help="NFR-4 长期回归台账")
    reg_sub = p_reg.add_subparsers(dest="regression_command", required=True)
    p_reg_run = reg_sub.add_parser(
        "run", help="比对一次并追加台账（不写基线）",
        description=("等价于 freeze-golden 的 dry-run 比对，并向 "
                     "artifacts/reports/regression/ledger.jsonl 追加记录。"
                     "退出码：0=delta 为 0；1=delta≠0；2=配置错误。"))
    p_reg_run.add_argument("--case", default="", help="只跑指定 case id（缺省跑全部）")
    p_reg_run.add_argument("--json", action="store_true", dest="json_out",
                           help="stdout 只输出一个 JSON 对象")
    p_reg_run.set_defaults(func=cmd_regression_run)

    p_reg_stats = reg_sub.add_parser(
        "stats", help="汇总台账（N<10 只报 k/N，不报比率）",
        description=("读台账并汇总。空台账不是错误（n=0，exit 0）；台账解析失败 exit 2。"
                     "N<10 时只输出 k/N，**不输出比率或百分数**。"))
    p_reg_stats.add_argument("--json", action="store_true", dest="json_out",
                             help="stdout 只输出一个 JSON 对象")
    p_reg_stats.set_defaults(func=cmd_regression_stats)

    # ---- FR-4 向量检索（第 36 轮）：索引可重建的可执行入口 ----
    p_rag = sub.add_parser("rag", help="RAG 向量检索（FR-4）")
    rag_sub = p_rag.add_subparsers(dest="rag_command", required=True)
    p_rag_rebuild = rag_sub.add_parser(
        "rebuild", help="重建本地向量索引（artifacts/rag/index/）",
        description=("从真题文本语料重建索引：实算每份语料的 sha256 并与语料清单比对，"
                     "不符即退出（exit 2，不写任何文件）。会调用 embedding 接口。"))
    p_rag_rebuild.add_argument("--corpus", default="artifacts/exam_corpus/text",
                               help="语料目录（含 manifest.json 与 <name>.txt）")
    p_rag_rebuild.add_argument("--out", default="artifacts/rag/index",
                               help="索引输出目录（manifest.json + vectors.jsonl）")
    p_rag_rebuild.add_argument("--chunk-chars", type=int, default=600, dest="chunk_chars",
                               help="块长（冻结默认 600）")
    p_rag_rebuild.add_argument("--overlap", type=int, default=80,
                               help="块重叠（冻结默认 80）")
    p_rag_rebuild.add_argument("--json", action="store_true", dest="json_out",
                               help="stdout 只输出 manifest 一个 JSON 对象")
    p_rag_rebuild.set_defaults(func=cmd_rag_rebuild)

    p_rag_search = rag_sub.add_parser(
        "search", help="在本地索引里检索（打印 rank/score/source）",
        description="读 artifacts/rag/index/ 并调 embedding 取查询向量；索引缺失 exit 2。")
    p_rag_search.add_argument("query", help="检索问句")
    p_rag_search.add_argument("--k", type=int, default=5, help="返回条数（1..20，默认 5）")
    p_rag_search.add_argument("--index", default="artifacts/rag/index", help="索引目录")
    p_rag_search.add_argument("--json", action="store_true", dest="json_out",
                              help="stdout 只输出一个 JSON 对象")
    p_rag_search.set_defaults(func=cmd_rag_search)

    p_report = sub.add_parser(
        "report", help="NFR-1 性能基线报告 / 仓库验收自证",
        description=("`--baseline`：报时延（按 model_tag 分组，nearest-rank 百分位）与"
                     "内存峰值 / tokens/s 的可报性。库或 metric_obs 表缺失 → exit 2；"
                     "**不输出全 0 的「基线」**。"
                     "`acceptance`：仓库验收自证 5 项（写 artifacts/reports/acceptance.md）；"
                     "任一 FAIL → exit 1。"))
    # **保持叶子命令**（不改容器）：`report --baseline` 已在用，改成 subparsers 会让它非法
    p_report.add_argument("action", nargs="?", choices=["acceptance"], default=None,
                          help="acceptance：仓库验收自证（写 artifacts/reports/acceptance.md）")
    p_report.add_argument("--out", default="artifacts/reports/acceptance.md",
                          help="验收报告落盘路径")
    p_report.add_argument("--baseline", action="store_true",
                          help="输出三项基线的可报性（内存峰值与 tokens/s 仅报不可报）")
    p_report.add_argument("--json", action="store_true", dest="json_out",
                          help="stdout 只输出一个 JSON 对象")
    p_report.add_argument("--db", default="data/mm.db", help="台账库")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口，返回退出码。"""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
