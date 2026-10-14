"""agents 基座：Ollama 客户端 + RoleRunner（JSON 强制、重试/降级、观测记录）。

硬约束：
- 只连 config/models.yaml 里的 base_url（默认 http://localhost:11434），模型名禁止硬编码；
- 输出必须是单个 JSON 对象：json.loads 失败 → temperature=0.0 重试 1 次
  → 再失败降级 fallback 模型；
- 每次调用尽力写 artifacts 与 metric_obs（prompt_version/model_tag/input_hash/latency_ms），
  观测失败不阻断主流程。
"""
from __future__ import annotations

import hashlib
import json
import re
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

# 角色默认 system 模板（prompts/<role>.system.j2 缺失时的兜底）
_DEFAULT_SYSTEM = (
    "你是数学建模竞赛协作 Agent 的 {{ role }} 角色。"
    "必须只输出一个 JSON 对象，不要输出任何其他文字。")


class LLMCallError(Exception):
    """LLM 调用最终失败（含重试与降级均失败）。"""


def load_models_config(path: str | Path = CONFIG_PATH) -> dict:
    """读取 models.yaml（base_url、模型名、角色预算的唯一来源）。"""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def strip_code_fence(text: str) -> str:
    """剥离 Markdown 代码围栏（coder 角色允许围栏输出）。"""
    m = re.search(r"```(?:\w+)?\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


class OllamaClient:
    """Ollama /api/chat 薄封装（httpx，base_url 来自配置）。"""

    def __init__(self, config: dict | None = None):
        cfg = (config or load_models_config())["ollama"]
        self.base_url = cfg.get("base_url", "http://localhost:11434")
        self.timeout = cfg.get("timeout_s", 120)
        self.think = cfg.get("think", None)
        self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def chat(self, model: str, messages: list[dict], temperature: float = 0.2,
             format: str = "json", timeout: float | None = None,
             num_predict: int | None = None) -> str:
        """返回 message.content 字符串（content 为空时兜底取 thinking）。

        注意：qwen3.5 等思考型模型开启思考时 content 恒为空、输出落在 thinking 且极易
        触达长度上限；因此按 config 的 `ollama.think` 显式控制，并用角色 max_tokens
        作为 num_predict 上限，避免生成被截断。
        """
        options: dict = {"temperature": temperature}
        if num_predict:
            options["num_predict"] = num_predict
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
        msg = resp.json().get("message", {}) or {}
        content = (msg.get("content") or "").strip()
        if not content:  # 兜底：思考型模型只填了 thinking
            content = (msg.get("thinking") or "").strip()
        return content


def _validate_required(obj: dict, schema: dict) -> list[str]:
    """轻量 required 校验（不引 jsonschema）：返回缺失键列表。"""
    return [k for k in schema.get("required", []) if k not in obj]


class RoleRunner:
    """单角色 LLM 调用器：prompt 渲染 → JSON 强制 → 重试/降级 → schema 校验 → 观测落库。"""

    def __init__(self, role: str, prompt_version: str, schema: dict | None = None,
                 config: dict | None = None, db_path: str | Path = DEFAULT_DB_PATH):
        self.role = role
        self.prompt_version = prompt_version  # 形如 <role>@x.y.z
        self.schema = schema or {}
        self.config = config or load_models_config()
        self.db_path = db_path
        self.client = OllamaClient(self.config)
        role_cfg = self.config.get("roles", {}).get(role, {})
        self.temperature = role_cfg.get("temperature", 0.2)
        self.max_tokens = role_cfg.get("max_tokens", 2048)

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

    def run(self, user_input: dict, context: dict | None = None,
            temperature: float | None = None) -> dict:
        """执行一次角色调用，返回校验后的 JSON dict；彻底失败抛 LLMCallError。

        temperature 传入时覆盖角色默认温度（首个尝试档），供「守卫不通过后降温重试」使用。
        """
        models = self.config["models"]
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
        input_hash = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        run_id = uuid.uuid4().hex
        last_err: Exception | None = None
        last_excerpt = ""

        for model_tag, temperature in chain:
            start = time.monotonic()
            raw = ""
            try:
                raw = self.client.chat(model_tag, messages,
                                       temperature=temperature, format="json",
                                       num_predict=self.max_tokens)
                # strict=False：容忍字符串值里的**裸控制字符（尤其真实换行）**。
                # 实测（task-003）qwen3.5 生成 code 字段时会间歇性把 "\n" 写成真实换行，
                # 严格解析直接 JSONDecodeError → 整个链路失败；这属于可无损容忍的格式噪声。
                obj = json.loads(strip_code_fence(raw), strict=False)
                if not isinstance(obj, dict):
                    raise ValueError("输出不是 JSON 对象")
                missing = _validate_required(obj, self.schema)
                if missing:
                    raise ValueError(f"缺少必需字段：{missing}")
                self._record(f"ollama/{model_tag}", input_hash,
                             round((time.monotonic() - start) * 1000, 1), run_id, True)
                return obj
            except Exception as exc:  # 网络/解析/校验失败均进下一档
                last_err = exc
                # 保留原始输出片段：否则 JSON/解析类故障只留一句报错，无法定位
                if raw:
                    last_excerpt = raw[:500]
                self._record(f"ollama/{model_tag}", input_hash,
                             round((time.monotonic() - start) * 1000, 1), run_id, False)
        detail = f"{last_err}"
        if last_excerpt:
            detail += f"\n原始输出片段（前 500 字符）：{last_excerpt!r}"
        raise LLMCallError(
            f"角色 {self.role}（{self.prompt_version}）调用失败（含降级）：{detail}")
