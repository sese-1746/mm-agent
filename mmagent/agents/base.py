"""agents 基座：模型客户端（DashScope 云 / Ollama 本地）+ RoleRunner（JSON 强制、重试/降级、观测记录）。

硬约束：
- 模型名与端点**一律来自 config/models.yaml**（禁止硬编码）；
- 后端由 `config/models.yaml` 的 `provider`（`dashscope` 默认 | `ollama`）选择，
  工厂 `make_client(config)` 是唯一构造入口；Ollama 分支语义保持不变（离线可切回）；
- **出网仅限白名单**：DashScope 端点 + 回环 `localhost`（见 `cli netguard-check` 名单制）；
- 云端密钥**只**从环境变量 `DASHSCOPE_API_KEY` 读取；缺失时抛 `MissingAPIKeyError`，
  **不得静默回退到 Ollama**；密钥不落盘、不写日志、不进报告；
- 云调用**之前**先跑 NFR-7 五类脱敏扫描（命中按 `dashscope.desensitize.on_hit` 阻断或掩码）；
- 每次**云端**调用向 `net_audit` 写一行（provider/endpoint/model/role/prompt_hash/ok/error_code）；
- 输出必须是单个 JSON 对象：json.loads 失败 → temperature=0.0 重试 1 次
  → 再失败降级 fallback 模型；
- 每次调用尽力写 artifacts 与 metric_obs（prompt_version/model_tag/input_hash/latency_ms），
  观测失败不阻断主流程。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml
from jinja2 import Template

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "models.yaml"
PROMPTS_DIR = Path(__file__).with_name("prompts")

# 云端密钥的环境变量名（**只**从环境变量读，不落盘）
DASHSCOPE_API_KEY_ENV = "DASHSCOPE_API_KEY"

# 角色默认 system 模板（prompts/<role>.system.j2 缺失时的兜底）
_DEFAULT_SYSTEM = (
    "你是数学建模竞赛协作 Agent 的 {{ role }} 角色。"
    "必须只输出一个 JSON 对象，不要输出任何其他文字。")


class LLMCallError(Exception):
    """LLM 调用最终失败（含重试与降级均失败）。"""


class MissingAPIKeyError(LLMCallError):
    """云端密钥缺失：显式报错，**不得**静默回退到本地 Ollama。"""


class TruncatedOutputError(LLMCallError):
    """最后一次尝试 `done_reason == "length"`：生成被长度上限/上下文挤压**硬截断**。

    与 JSON 语法错的区分是本类的意义：截断必然导致 JSON 解析失败，但「截断」是可诊断、
    可动作（抬高 num_ctx）的，而「模型写出畸形 JSON」不是。
    """


class LLMOutputSyntaxError(LLMCallError):
    """最后一次尝试是 JSON 解析失败/非 JSON 对象（非截断导致）。"""


class LLMBudgetExceeded(LLMCallError):
    """本 run 该角色的调用预算已用尽：**调用未发生**，直接拒绝。

    与另外三个子类的区别：那三个是「调用失败后」的分类，本类是「调用前」的拒绝。
    仍是 `LLMCallError` 子类 → 现有 `except LLMCallError`（coder.py:57 / cli.py:978）行为不变。

    属性 `error_code` / `http_status` 由 `app.py::_unhandled_handler` 鸭子类型取用
    （499 非 RFC 9110 标准码，借 nginx 的 `client closed request` 语义表示预算超限）。
    """

    error_code = "LLM_BUDGET_EXCEEDED"
    http_status = 499


# 失败原始输出的落盘目录（完整 raw，便于定位断点；不落 500 字符截断）
FAILURE_DUMP_DIR = REPO_ROOT / "artifacts" / "reports" / "llm_failures"


def load_models_config(path: str | Path = CONFIG_PATH) -> dict:
    """读取 models.yaml（provider、base_url、模型名、角色预算的唯一来源）。"""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def resolve_provider(config: dict) -> str:
    """判定运行时后端：`dashscope`（云，默认）| `ollama`（本地）。

    显式 `provider` 优先；未声明时按配置结构回退（含 `dashscope:` 段 → 云，否则 Ollama），
    以兼容既有手写配置（如测试夹具只给 `ollama:`/`models:`）。
    """
    provider = config.get("provider")
    if provider:
        return str(provider)
    return "dashscope" if "dashscope" in config else "ollama"


def provider_models(config: dict) -> dict:
    """按 provider 取出 `{generation, fallback, embedding}` 模型名（**唯一**来源，禁止硬编码）。"""
    if resolve_provider(config) == "ollama":
        return dict(config.get("models") or {})
    ds = config.get("dashscope") or {}
    legacy = config.get("models") or {}
    return {
        "generation": ds.get("model_generation") or legacy.get("generation"),
        "fallback": ds.get("model_fallback") or legacy.get("fallback"),
        "embedding": ds.get("embedding") or legacy.get("embedding"),
    }


def role_settings(config: dict, provider: str, role: str) -> dict:
    """取角色预算：优先 `<provider>.roles[role]`，回退顶层 `roles[role]`。"""
    per_provider = (config.get(provider) or {}).get("roles") or {}
    return dict(per_provider.get(role) or (config.get("roles") or {}).get(role) or {})


def record_net_audit(db_path: str | Path, *, provider: str, endpoint: str, model: str,
                     role: str, prompt_hash: str, ok: bool, error_code: str = "") -> None:
    """向 `net_audit` 写一行出网审计（**尽力而为**：失败只写 stderr，**不抛**、不阻断主流程）。

    仅记录云端出网调用（本地 Ollama 不出网，不入台账）。
    失败可见化（A0-2）：审计是 FR-13 / NFR-2 的凭据，静默丢失 = 台账说谎；因此异常**不再**被
    无声吞掉，而是打一行含 `provider`/`model`/`role` 与异常摘要的 stderr（仍不改「不抛」语义）。
    """
    if provider == "ollama":
        return
    try:
        conn = connect(db_path)
        try:
            conn.execute(
                "INSERT INTO net_audit (ts, provider, endpoint, model, role,"
                " prompt_hash, ok, error_code) VALUES (?,?,?,?,?,?,?,?)",
                (utcnow(), provider, endpoint, model, role, prompt_hash,
                 1 if ok else 0, error_code or ""))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001  尽力而为：不阻断主流程
        print(f"[net_audit] 审计写入失败（provider={provider} model={model} role={role} "
              f"ok={ok} error_code={error_code or '-'}）：{type(exc).__name__}: {exc}",
              file=sys.stderr)


def strip_code_fence(text: str) -> str:
    """剥离 Markdown 代码围栏（coder 角色允许围栏输出）。"""
    m = re.search(r"```(?:\w+)?\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


class OllamaClient:
    """Ollama /api/chat 薄封装（httpx，base_url 来自配置）。本地后端，语义保持不变。"""

    provider = "ollama"

    def __init__(self, config: dict | None = None):
        cfg = (config or load_models_config())["ollama"]
        self.base_url = cfg.get("base_url", "http://localhost:11434")
        self.timeout = cfg.get("timeout_s", 120)
        self.num_ctx = cfg.get("num_ctx")      # 运行态上下文窗口（None=用 Ollama 默认）
        self.think = cfg.get("think", None)
        self.endpoint = self.base_url.rstrip("/") + "/api/chat"
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def chat_meta(self, model: str, messages: list[dict], temperature: float = 0.2,
                  format: str = "json", timeout: float | None = None,
                  num_predict: int | None = None) -> tuple[str, dict]:
        """同 chat()，但额外返回响应元数据 meta（用于截断判定与观测）。

        meta 键：done_reason / eval_count / prompt_eval_count /
                 total_duration_ns / load_duration_ns / think_sent / options_sent。
        `done_reason == "length"` 表示生成被长度上限或上下文挤压硬截断
        （Ollama 运行态 ctx 默认 4096，若 num_predict 更大且 prompt 长，生成空间被挤压）。
        """
        options: dict = {"temperature": temperature}
        if num_predict:
            options["num_predict"] = num_predict
        if self.num_ctx:
            # 必须显式传：Ollama 运行态默认 ctx=4096，若 num_predict 更大，
            # 生成空间被挤压 → 输出在 JSON 中途被截断（"Unterminated string"）。
            options["num_ctx"] = int(self.num_ctx)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if format:
            payload["format"] = format
        if self.think is not None:
            payload["think"] = self.think
        resp = self._client.post("/api/chat", json=payload,
                                 timeout=timeout or self.timeout)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {}) or {}
        content = (msg.get("content") or "").strip()
        if not content:  # 兜底：思考型模型只填了 thinking
            content = (msg.get("thinking") or "").strip()
        meta = {
            "done_reason": data.get("done_reason"),
            "eval_count": data.get("eval_count"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "total_duration_ns": data.get("total_duration"),
            "load_duration_ns": data.get("load_duration"),
            "think_sent": self.think is not None,
            "options_sent": dict(options),
        }
        return content, meta

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2,
             format: str = "json", timeout: float | None = None,
             num_predict: int | None = None) -> str:
        """返回 message.content 字符串（content 为空时兜底取 thinking）。

        注意：qwen3.5 等思考型模型开启思考时 content 恒为空、输出落在 thinking 且极易
        触达长度上限；因此按 config 的 `ollama.think` 显式控制，并用角色 max_tokens
        作为 num_predict 上限，避免生成被截断。

        签名与返回类型保持不变；内部改为调用 chat_meta 并只取 content。
        """
        content, _ = self.chat_meta(model, messages, temperature=temperature,
                                    format=format, timeout=timeout,
                                    num_predict=num_predict)
        return content

    def embed(self, texts: list[str], model: str | None = None,
              timeout: float | None = None) -> list[list[float]]:
        """Ollama `/api/embed` 取向量（FR-4 RAG）。返回与 `texts` **同序同长**的向量列表。

        `model` 缺省取 `provider_models()["embedding"]`（模型名唯一来源，
        **禁止**在此硬编码）。
        """
        name = model or provider_models(load_models_config())["embedding"]
        if not name:
            raise LLMCallError("config/models.yaml 未配置 ollama.models.embedding")
        resp = self._client.post("/api/embed",
                                 json={"model": name, "input": list(texts)},
                                 timeout=timeout or self.timeout)
        resp.raise_for_status()
        data = resp.json()
        vectors = data.get("embeddings")
        if vectors is None:  # 老版本 Ollama 用单条 `embedding` 字段
            single = data.get("embedding")
            vectors = [single] if single else []
        return [[float(x) for x in vec] for vec in vectors]


class DashScopeClient:
    """阿里云 DashScope（OpenAI 兼容端点 `/chat/completions`）薄封装。

    - 与 `OllamaClient` **同一 `chat_meta()` 签名**，返回 `(content, meta)`；
    - 密钥**只**从环境变量 `DASHSCOPE_API_KEY` 读取；缺失 → `MissingAPIKeyError`
      （**不**回退 Ollama、不落盘、不打印）；
    - `meta` 与 Ollama 分支对齐关键键：`done_reason`（`"length"` 表示被长度硬截断）、
      `eval_count` / `prompt_eval_count`；另附 `finish_reason` / `total_duration_ns`；
    - 出网目标仅 `base_url`（白名单内的 DashScope 域名）。
    """

    provider = "dashscope"

    def __init__(self, config: dict | None = None, *, client: httpx.Client | None = None,
                 api_key: str | None = None):
        cfg = (config or load_models_config()).get("dashscope") or {}
        self.base_url = str(cfg.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise ValueError("config/models.yaml 缺少 dashscope.base_url（禁止硬编码端点）")
        self.timeout = cfg.get("timeout_s", 300)
        key = api_key if api_key is not None else os.environ.get(DASHSCOPE_API_KEY_ENV)
        if not key:
            raise MissingAPIKeyError(
                f"环境变量 {DASHSCOPE_API_KEY_ENV} 缺失：无法调用云端模型；"
                "如需离线运行请在 config/models.yaml 设 provider: ollama（不静默回退）")
        self._api_key = key
        self.endpoint = self.base_url + "/chat/completions"
        self._client = client or httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def chat_meta(self, model: str, messages: list[dict], temperature: float = 0.2,
                  format: str = "json", timeout: float | None = None,
                  num_predict: int | None = None) -> tuple[str, dict]:
        """同 OllamaClient.chat_meta 的签名与返回形状（content, meta）。"""
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if num_predict:
            payload["max_tokens"] = num_predict
        if format == "json":
            # OpenAI 兼容端点：要求返回严格 JSON 对象
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        start = time.monotonic()
        resp = self._client.post("/chat/completions", json=payload,
                                 headers=headers, timeout=timeout or self.timeout)
        resp.raise_for_status()
        data = resp.json()
        latency_s = time.monotonic() - start
        choices = data.get("choices") or [{}]
        choice = choices[0] or {}
        msg = choice.get("message") or {}
        content = (msg.get("content") or "").strip()
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage") or {}
        meta = {
            # 与 Ollama 分支对齐：done_reason == "length" 即「被长度硬截断」
            "done_reason": "length" if finish_reason == "length" else (finish_reason or "stop"),
            "finish_reason": finish_reason,
            "eval_count": usage.get("completion_tokens"),
            "prompt_eval_count": usage.get("prompt_tokens"),
            "total_duration_ns": int(latency_s * 1e9),
            "load_duration_ns": None,
            "think_sent": False,
            "options_sent": {k: v for k, v in payload.items()
                             if k in ("temperature", "max_tokens", "response_format")},
        }
        return content, meta

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2,
             format: str = "json", timeout: float | None = None,
             num_predict: int | None = None) -> str:
        """返回 content 字符串（与 OllamaClient.chat 同签名）。"""
        content, _ = self.chat_meta(model, messages, temperature=temperature,
                                    format=format, timeout=timeout,
                                    num_predict=num_predict)
        return content

    def embed(self, texts: list[str], model: str | None = None,
              timeout: float | None = None) -> list[list[float]]:
        """DashScope `/embeddings` 取向量（FR-4 RAG）。返回与 `texts` **同序同长**的向量列表。

        请求体 `{"model": .., "input": [..]}`，响应 `{"data": [{"embedding": [..]}, ..]}`；
        `data` 按 `index` 排序后再取，避免服务端乱序时「向量贴错文本」这种静默错配。
        `model` 缺省取 `provider_models()["embedding"]`（模型名唯一来源，禁止硬编码）。
        """
        name = model or provider_models(load_models_config())["embedding"]
        if not name:
            raise LLMCallError("config/models.yaml 未配置 dashscope.embedding")
        payload = {"model": name, "input": list(texts)}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        resp = self._client.post("/embeddings", json=payload, headers=headers,
                                 timeout=timeout or self.timeout)
        resp.raise_for_status()
        data = resp.json()
        items = list(data.get("data") or [])
        items.sort(key=lambda item: item.get("index", 0))
        return [[float(x) for x in (item.get("embedding") or [])] for item in items]


def make_client(config: dict | None = None) -> OllamaClient | DashScopeClient:
    """按 `config["provider"]` 构造客户端（**唯一**入口）：`dashscope` | `ollama`。"""
    cfg = config or load_models_config()
    provider = resolve_provider(cfg)
    if provider == "ollama":
        return OllamaClient(cfg)
    if provider == "dashscope":
        return DashScopeClient(cfg)
    raise ValueError(f"未知 provider：{provider!r}（应为 'dashscope' 或 'ollama'）")


def _preflight_desensitize(messages: list[dict], config: dict, db_path: str | Path,
                           role: str) -> list[dict]:
    """云调用**前**的 NFR-7 脱敏前置。

    对 messages 的文本跑五类模式扫描（复用 `security/desensitize.py`）：
    - 命中即写 `desensitize_hit` 台账；
    - `dashscope.desensitize.on_hit`：`block`（默认）→ 抛 `DesensitizeBlocked`；
      `mask` → 掩码后继续送云。
    未命中则原样返回 messages。
    """
    from ..security import desensitize as ds

    cfg = (config.get("dashscope") or {}).get("desensitize") or {}
    on_hit = str(cfg.get("on_hit", "block")).lower()
    out: list[dict] = []
    hits: list[dict] = []
    for msg in messages:
        content = str(msg.get("content") or "")
        masked, msg_hits = ds.scan_and_mask([content], field_raw=f"llm:{role}")
        hits.extend(msg_hits)
        out.append({**msg, "content": masked[0] if masked else content})
    if not hits:
        return messages
    ds.record_hits(hits, db_path=db_path)
    if on_hit != "mask":
        raise ds.DesensitizeBlocked(hits, where=f"llm 调用（role={role}）")
    return out


def _validate_required(obj: dict, schema: dict) -> list[str]:
    """轻量 required 校验（不引 jsonschema）：返回缺失键列表。"""
    return [k for k in schema.get("required", []) if k not in obj]


class RoleRunner:
    """单角色 LLM 调用器：prompt 渲染 → JSON 强制 → 重试/降级 → schema 校验 → 观测落库。"""

    def __init__(self, role: str, prompt_version: str, schema: dict | None = None,
                 config: dict | None = None, db_path: str | Path = DEFAULT_DB_PATH,
                 client: OllamaClient | DashScopeClient | None = None,
                 run_id: str | None = None):
        self.role = role
        self.prompt_version = prompt_version  # 形如 <role>@x.y.z
        self.schema = schema or {}
        self.config = config or load_models_config()
        self.db_path = db_path
        self.provider = resolve_provider(self.config)
        # client 可注入（离线测试用假客户端）；缺省走工厂按 provider 选择
        self.client = client or make_client(self.config)
        role_cfg = role_settings(self.config, self.provider, role)
        self.temperature = role_cfg.get("temperature", 0.2)
        self.max_tokens = role_cfg.get("max_tokens", 2048)
        # 单轮任务调用预算（config/models.yaml 的 call_budget，**唯一真源**）。
        # 缺失/0/负数/非整数一律视为**不限额**（见 `_check_budget`）。
        self.call_budget = role_cfg.get("call_budget")
        # run_id：调用方显式传入则全链路复用（一次 CLI solve 只产生一个 run_id）；
        # 未传时 `run()` 内保持原有的 uuid4 生成行为（向后兼容）。
        self.run_id = run_id

    def _render_system(self, context: dict) -> str:
        tpl_path = PROMPTS_DIR / f"{self.role}.system.j2"
        src = (tpl_path.read_text(encoding="utf-8")
               if tpl_path.exists() else _DEFAULT_SYSTEM)
        return Template(src).render(role=self.role, **context)

    def _record(self, model_tag: str, input_hash: str, latency_ms: float,
                run_id: str, ok: bool) -> None:
        """观测落库（尽力而为，失败静默）。"""
        try:
            conn = connect(self.db_path)
            try:
                conn.execute(
                    "INSERT INTO metric_obs (ts, metric, value, unit, run_id,"
                    " prompt_version, model_tag) VALUES (?,?,?,?,?,?,?)",
                    (utcnow(), "llm_latency", latency_ms, "ms", run_id,
                     self.prompt_version, model_tag))
                conn.execute(
                    "INSERT INTO artifacts (ts, kind, path, producer, prompt_version,"
                    " model_tag, input_hash) VALUES (?,?,?,?,?,?,?)",
                    (utcnow(), "llm_output", "", self.role, self.prompt_version,
                     model_tag, input_hash))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _record_meta(self, model_tag: str, run_id: str, meta: dict) -> None:
        """把每次尝试的 token/截断指标写入 metric_obs（复用现有表，**不改 schema**）。

        指标：llm_prompt_eval_count / llm_eval_count / llm_truncated(0/1)。
        尽力而为，失败静默（与 _record 一致，观测不阻断主流程）。
        """
        try:
            rows = [
                ("llm_prompt_eval_count", meta.get("prompt_eval_count"), "count"),
                ("llm_eval_count", meta.get("eval_count"), "count"),
                ("llm_truncated", 1 if meta.get("done_reason") == "length" else 0, "bool"),
            ]
            conn = connect(self.db_path)
            try:
                for metric, value, unit in rows:
                    if value is None and metric != "llm_truncated":
                        continue  # 元数据缺失时不写（避免伪造 0）
                    conn.execute(
                        "INSERT INTO metric_obs (ts, metric, value, unit, run_id,"
                        " prompt_version, model_tag) VALUES (?,?,?,?,?,?,?)",
                        (utcnow(), metric, value, unit, run_id,
                         self.prompt_version, model_tag))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _budget_used(self, run_id: str) -> int:
        """本 run 本角色已发生的 attempt 次数（口径 = `metric_obs` 每 attempt 一行）。

        `_record` 每次尝试（成功或失败）都写一行 `metric='llm_latency'`，所以
        `COUNT(*)` 就是 attempt 数。`metric_obs` **没有 role 列**，角色用
        `prompt_version`（形如 `<role>@x.y.z`）归因。

        读失败按 0 计（与 `_record` 的「观测不阻断主流程」一致）→ 退化为不限额，
        不因台账不可用把正常调用打死。
        """
        try:
            conn = connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM metric_obs"
                    " WHERE run_id = ? AND metric = ? AND prompt_version = ?",
                    (run_id, "llm_latency", self.prompt_version)).fetchone()
                return int(row["n"])
            finally:
                conn.close()
        except Exception:
            return 0

    def _check_budget(self, run_id: str) -> None:
        """每次 attempt **之前**判定预算；越限抛 `LLMBudgetExceeded`（调用不发出）。

        `call_budget` 缺失 / 0 / 负数 / 非整数 → **不限额**（不改判成「一次都不许调」）。
        """
        budget = self.call_budget
        if budget is None:
            return
        try:
            limit = int(budget)
        except (TypeError, ValueError):
            return
        if limit <= 0:
            return
        used = self._budget_used(run_id)
        if used >= limit:
            raise LLMBudgetExceeded(
                f"角色 {self.role}（{self.prompt_version}）调用预算耗尽："
                f"已用 {used} / 上限 {limit}（run_id={run_id}）")

    def _dump_raw(self, raw: str, model_tag: str) -> str | None:
        """把**完整** raw 落盘，返回路径；失败只打一条 stderr 警告，不影响异常语义。"""
        try:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", model_tag)
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            FAILURE_DUMP_DIR.mkdir(parents=True, exist_ok=True)
            path = FAILURE_DUMP_DIR / f"{ts}_{self.role}_{safe}.txt"
            path.write_text(raw, encoding="utf-8")
            return str(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[RoleRunner] 完整 raw 落盘失败：{exc}", file=sys.stderr)
            return None

    def run(self, user_input: dict, context: dict | None = None,
            temperature: float | None = None) -> dict:
        """执行一次角色调用，返回校验后的 JSON dict；彻底失败抛 LLMCallError 子类。

        temperature 传入时覆盖角色默认温度（首个尝试档），供「守卫不通过后降温重试」使用。

        失败分类（**按最后一次尝试的原因**）：
        - 最后一次 `done_reason == "length"` → `TruncatedOutputError`（截断优先判定）
        - 最后一次是 JSON 解析失败/非 dict   → `LLMOutputSyntaxError`
        - 其它（schema 缺字段 / 网络 / 超时）→ `LLMCallError`
        三者均为 `LLMCallError` 子类，故现有 `except LLMCallError`（coder.py）行为不变。
        三档链结构/次数**未改动**（只加分类与观测）。
        云端（provider=dashscope）另有**脱敏前置**（NFR-7）与 **net_audit** 出网台账；
        本地 Ollama 分支行为保持不变（不出网、不入 net_audit）。
        """
        models = provider_models(self.config)
        first_temp = self.temperature if temperature is None else temperature
        chain = [  # (模型, 温度)：主模型 → 降温重试 → 降级模型
            (models["generation"], first_temp),
            (models["generation"], 0.0),
            (models["fallback"], 0.0),
        ]
        system = self._render_system(context or {})
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_input, ensure_ascii=False)},
        ]
        # 云调用前的 NFR-7 脱敏前置：命中 → 记 desensitize_hit + 按配置阻断/掩码
        if self.provider != "ollama":
            messages = _preflight_desensitize(messages, self.config, self.db_path, self.role)
        endpoint = getattr(self.client, "endpoint", "")
        input_hash = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        run_id = self.run_id or uuid.uuid4().hex
        last_err: Exception | None = None
        last_excerpt = ""
        last_raw = ""
        last_kind = "other"          # truncated | syntax | schema | other
        last_model = chain[-1][0]

        for model_tag, temperature in chain:
            self._check_budget(run_id)      # 越限即抛（此时**尚未**发出调用）
            start = time.monotonic()
            raw = ""
            meta: dict = {}
            kind = "other"
            tag = f"{self.provider}/{model_tag}"
            try:
                raw, meta = self.client.chat_meta(
                    model_tag, messages, temperature=temperature, format="json",
                    num_predict=self.max_tokens)
                try:
                    # strict=False：容忍字符串值里的**裸控制字符（尤其真实换行）**。
                    # 实测（task-003）qwen3.5 生成 code 字段时会间歇性把 "\n" 写成真实换行，
                    # 严格解析直接 JSONDecodeError → 整个链路失败；这属于可无损容忍的格式噪声。
                    obj = json.loads(strip_code_fence(raw), strict=False)
                except json.JSONDecodeError:
                    # 截断必然导致 JSON 失败 → 若 done_reason=="length" 优先判为「截断」
                    kind = "truncated" if meta.get("done_reason") == "length" else "syntax"
                    raise
                if not isinstance(obj, dict):
                    kind = "truncated" if meta.get("done_reason") == "length" else "syntax"
                    raise ValueError("输出不是 JSON 对象")
                missing = _validate_required(obj, self.schema)
                if missing:
                    kind = "schema"
                    raise ValueError(f"缺少必需字段：{missing}")
                self._record(tag, input_hash,
                             round((time.monotonic() - start) * 1000, 1), run_id, True)
                self._record_meta(tag, run_id, meta)
                record_net_audit(self.db_path, provider=self.provider, endpoint=endpoint,
                                 model=model_tag, role=self.role, prompt_hash=input_hash, ok=True)
                return obj
            except Exception as exc:  # 网络/解析/校验失败均进下一档
                last_err, last_kind, last_model = exc, kind, model_tag
                # 保留原始输出片段：否则 JSON/解析类故障只留一句报错，无法定位
                if raw:
                    last_excerpt = raw[:500]
                    last_raw = raw
                self._record(tag, input_hash,
                             round((time.monotonic() - start) * 1000, 1), run_id, False)
                self._record_meta(tag, run_id, meta)
                record_net_audit(self.db_path, provider=self.provider, endpoint=endpoint,
                                 model=model_tag, role=self.role, prompt_hash=input_hash,
                                 ok=False, error_code=kind)

        detail = f"{last_err}"
        if last_excerpt:
            detail += f"\n原始输出片段（前 500 字符）：{last_excerpt!r}"
        if last_raw:
            dump = self._dump_raw(last_raw, last_model)
            if dump:
                detail += f"\n完整原始输出：{dump}"
        message = (f"角色 {self.role}（{self.prompt_version}）调用失败（含降级）：{detail}")
        if last_kind == "truncated":
            raise TruncatedOutputError(message)
        if last_kind == "syntax":
            raise LLMOutputSyntaxError(message)
        raise LLMCallError(message)
