"""RAG 概念问答路由：检索唯一本地知识库，再由配置中的聊天模型作答。

问答请求允许把图片和问题一起提交。图片只作为本次问答的多模态补充输入，
不会创建第二个知识库，也不会把图片落盘。
"""
from __future__ import annotations

import base64
import binascii
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agents.base import load_models_config, make_client, provider_models
from ..rag import adapter as rag_adapter

router = APIRouter(prefix="/api/v1", tags=["rag"])

K_MIN = 1
K_MAX = 20
K_DEFAULT = 5
MAX_QUESTION_CHARS = 10_000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_TOTAL_BYTES = 20 * 1024 * 1024
MAX_ERROR_MESSAGE = 200
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


class RagAskImage(BaseModel):
    """一次问答中的图片，b64 不含 data URL 前缀。"""

    name: str = Field(default="image", max_length=200)
    mime: str = Field(default="image/png", max_length=100)
    b64: str = Field(min_length=1)


class RagAskRequest(BaseModel):
    """概念问答请求；校验在路由函数体中完成，保持统一 422 错误体可序列化。"""

    question: str
    k: int = K_DEFAULT
    images: list[RagAskImage] = Field(default_factory=list)


def _bad(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status,
                         detail={"error_code": code, "message": message})


def _decode_images(images: list[RagAskImage]) -> list[dict]:
    if len(images) > MAX_IMAGES:
        raise _bad(422, "RAG_ASK_INVALID", f"一次最多上传 {MAX_IMAGES} 张图片")
    decoded: list[dict] = []
    total = 0
    for item in images:
        mime = str(item.mime or "").lower().strip()
        if mime not in ALLOWED_IMAGE_MIMES:
            raise _bad(422, "RAG_ASK_INVALID", f"不支持的图片类型：{item.mime}")
        try:
            raw = base64.b64decode(item.b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _bad(422, "RAG_ASK_INVALID", f"图片不是有效的 base64：{item.name}") from exc
        if not raw:
            raise _bad(422, "RAG_ASK_INVALID", f"图片内容为空：{item.name}")
        if len(raw) > MAX_IMAGE_BYTES:
            raise _bad(422, "RAG_ASK_INVALID", f"单张图片不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB：{item.name}")
        total += len(raw)
        if total > MAX_IMAGE_TOTAL_BYTES:
            raise _bad(422, "RAG_ASK_INVALID", "本次图片总大小不能超过 20 MB")
        decoded.append({"name": str(item.name or "image"), "mime": mime,
                        "b64": item.b64, "bytes": len(raw)})
    return decoded


def _context_prompt(hits: list[dict]) -> str:
    if not hits:
        return "（本次检索没有返回知识库片段。）"
    blocks = []
    for idx, hit in enumerate(hits, 1):
        source = hit.get("source") or hit.get("path") or "未知来源"
        text = hit.get("text") or hit.get("preview") or ""
        blocks.append(f"[片段{idx}] 来源：{source}\n{text}")
    return "\n\n".join(blocks)


def _build_messages(question: str, hits: list[dict], images: list[dict]) -> list[dict]:
    """构造同时支持 Ollama 与 DashScope 的单次多模态消息。"""
    prompt = (
        "请用中文回答用户的概念问题。只能依据下面的知识库片段作答；"
        "如果片段不足以支持结论，请明确说‘知识库里没有找到相关信息’，不要编造。"
        "回答中的事实尽量引用对应的[片段N]。图片是本次问题的补充材料，不得替代知识库依据。\n\n"
        f"知识库片段：\n{_context_prompt(hits)}\n\n用户问题：\n{question}"
    )
    if not images:
        return [{"role": "user", "content": prompt}]
    return [{"role": "user", "content": prompt,
             "images": [item["b64"] for item in images]}]


def _build_dashscope_messages(question: str, hits: list[dict], images: list[dict]) -> list[dict]:
    """将同一语义消息转成 OpenAI 兼容的 image_url 结构。"""
    messages = _build_messages(question, hits, [])
    if not images:
        return messages
    content = [{"type": "text", "text": messages[0]["content"]}]
    content.extend({"type": "image_url", "image_url": {
        "url": f"data:{item['mime']};base64,{item['b64']}"}}
                   for item in images)
    return [{"role": "user", "content": content}]


@router.post("/rag/ask")
def rag_ask(req: RagAskRequest) -> dict:
    """在唯一 RAG 知识库中检索，并完成一次文本或图文概念问答。"""
    question = (req.question or "").strip()
    if not question:
        raise _bad(422, "RAG_ASK_INVALID", "question 不能为空")
    if len(question) > MAX_QUESTION_CHARS:
        raise _bad(422, "RAG_ASK_INVALID", f"question 不能超过 {MAX_QUESTION_CHARS} 个字符")
    if not K_MIN <= req.k <= K_MAX:
        raise _bad(422, "RAG_ASK_INVALID", f"k 必须在 {K_MIN}..{K_MAX} 之间，收到 {req.k}")
    images = _decode_images(req.images)

    started = time.monotonic()
    try:
        retrieved = rag_adapter.search(question, req.k)
    except rag_adapter.IndexMissingError as exc:
        raise _bad(404, "RAG_INDEX_MISSING", str(exc)) from exc
    except rag_adapter.IndexCorruptError as exc:
        raise _bad(503, "RAG_INDEX_CORRUPT", str(exc)[:MAX_ERROR_MESSAGE]) from exc
    except rag_adapter.RagEmbedFailed as exc:
        raise _bad(502, "RAG_EMBED_FAILED", str(exc)[:MAX_ERROR_MESSAGE]) from exc

    try:
        config = load_models_config()
        client = make_client(config)
        model = provider_models(config).get("generation")
        if not model:
            raise ValueError("config/models.yaml 未配置 generation 模型名")
        if getattr(client, "provider", "") == "dashscope":
            messages = _build_dashscope_messages(question, retrieved["hits"], images)
        else:
            messages = _build_messages(question, retrieved["hits"], images)
        answer = client.chat(model, messages, temperature=0.2, format="",
                             num_predict=1200).strip()
        if not answer:
            raise ValueError("聊天模型返回空答案")
    except Exception as exc:  # noqa: BLE001 - 问答调用统一转为可诊断的 502
        raise _bad(502, "RAG_ANSWER_FAILED", f"{type(exc).__name__}: {exc}"[:MAX_ERROR_MESSAGE]) from exc

    return {
        "ok": True,
        "question": question,
        "answer": answer,
        "model": model,
        "knowledge_base": "artifacts/rag/index",
        "hits": retrieved["hits"],
        "index": retrieved["index"],
        "images_used": [{"name": item["name"], "mime": item["mime"]} for item in images],
        "latency_ms": int(round((time.monotonic() - started) * 1000)),
    }


__all__ = ["RagAskImage", "RagAskRequest", "rag_ask", "_build_messages",
           "_build_dashscope_messages"]
