"""RAG 适配层：加载本地向量索引 → 取查询向量 → 余弦检索。

**唯一**允许触碰 `INDEX_ROOT` 的模块（其余模块只调 `search()`）。设计要点：

- `INDEX_ROOT` 是**模块级常量**：测试/探针可 `monkeypatch.setattr(adapter, "INDEX_ROOT", tmp)`，
  绝不硬编码进函数体；
- 索引缺失 → `IndexMissingError`（**显式异常**，不是静默返回 `[]`：
  「检索不到」与「没有索引」是两件事，混成一个空列表会让调用方以为语料里真没有相关内容）；
- 索引存在但内部不自洽（行数/维度/缺键）→ `IndexCorruptError`（路由 → `503`；
  不许静默少检索，也不许让 `cosine` 的维度错兜成「500 + 类名」）；
- 取向量失败 → `RagEmbedFailed`（路由翻成 `502 RAG_EMBED_FAILED`，不落成 500 + 类名）；
- `embed_fn` 可注入（离线用例完全不联网）；缺省走 `embed_texts()` —— 按
  `config/models.yaml` 的 provider 造客户端、模型名取 `provider_models()["embedding"]`。

索引本身的格式/分块/写盘全在 `tools/rag_index_tool.py`（零联网零 LLM），本层只做**编排**：
读索引 → 调模型 → 排序 → 组响应。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..agents.base import load_models_config, make_client, provider_models
from ..tools import rag_index_tool
from ..tools.rag_index_tool import (IndexCorruptError, IndexMissingError,
                                    RagIndexError)

REPO_ROOT = Path(__file__).resolve().parents[2]

# 索引根目录（唯一引用点；测试 monkeypatch 到这里即可整体切到 tmp_path）
INDEX_ROOT = REPO_ROOT / "artifacts" / "rag" / "index"


class RagEmbedFailed(RagIndexError):
    """取查询向量失败（密钥缺失 / 网络错 / 维度与索引不符）：路由翻 502。"""


def embed_texts(texts: list[str]) -> list[list[float]]:
    """默认 embedder：按 provider 造客户端，模型名取 `provider_models()["embedding"]`。

    **唯一**的真实出网点（`agents/base.py` 的 `embed()`）；测试用 monkeypatch 换掉它。
    """
    cfg = load_models_config()
    client = make_client(cfg)
    model = provider_models(cfg)["embedding"]
    if not model:
        raise RagEmbedFailed("config/models.yaml 未配置 embedding 模型名（provider="
                            f"{cfg.get('provider')!r}）")
    return client.embed(texts)


def _embed_query(query: str, embed_fn: Callable[[list[str]], list[list[float]]] | None,
                 dims: int | None) -> list[float]:
    """取查询向量；任何失败都归一成 `RagEmbedFailed`（message ≤200 字符，路由照抄）。"""
    fn = embed_fn or embed_texts
    try:
        vectors = fn([query])
    except RagEmbedFailed:
        raise
    except Exception as exc:  # noqa: BLE001  取向量失败要显式降级成契约里的 502
        raise RagEmbedFailed(f"{type(exc).__name__}: {exc}"[:200]) from exc
    if not vectors:
        raise RagEmbedFailed("embedding 返回空列表（请求 1 条，返回 0 条）")
    vec = [float(x) for x in vectors[0]]
    if not vec:
        raise RagEmbedFailed("embedding 返回空向量")
    if dims is not None and len(vec) != int(dims):
        raise RagEmbedFailed(
            f"查询向量维度 {len(vec)} 与索引 {dims} 不一致：向量模型可能已更换，"
            "请先跑 cli rag rebuild 重建索引")
    return vec


def search(query: str, k: int = 5, *, index_root: str | Path | None = None,
           embed_fn: Callable[[list[str]], list[list[float]]] | None = None) -> dict:
    """在本地向量索引里检索最相关的 k 条片段。

    返回 `{"query", "k", "hits", "index"}`：
    - `hits` 按余弦**降序**（`rank` 从 1 起，`score` 保留 4 位）；语料里确实没有相关内容时
      可以是空列表 —— 那仍然是**成功**（`ok=true`）；
    - `index` 是索引摘要（path/chunks/dims/model/built_at/corpus_sha256）。

    索引不存在抛 `IndexMissingError`（路由 → `404 RAG_INDEX_MISSING`）；
    取向量失败抛 `RagEmbedFailed`（路由 → `502 RAG_EMBED_FAILED`）。
    """
    root = Path(index_root) if index_root is not None else INDEX_ROOT
    manifest, records = rag_index_tool.load_index(root)
    vec = _embed_query(query, embed_fn, manifest.get("dims"))
    hits = rag_index_tool.rank(vec, records, k)
    return {
        "query": query,
        "k": k,
        "hits": hits,
        "index": {
            "path": rag_index_tool.display_path(root),
            "chunks": manifest.get("chunks"),
            "dims": manifest.get("dims"),
            "model": manifest.get("model"),
            "built_at": manifest.get("built_at"),
            "corpus_sha256": rag_index_tool.corpus_digest(manifest.get("corpus") or []),
        },
    }


def rebuild(corpus_dir: str | Path, out_dir: str | Path, *,
            embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
            chunk_chars: int = rag_index_tool.DEFAULT_CHUNK_CHARS,
            chunk_overlap: int = rag_index_tool.DEFAULT_CHUNK_OVERLAP,
            built_at: str | None = None) -> dict:
    """重建索引（`cli rag rebuild` 的唯一实现；索引格式留给工具层）。

    模型名**仍**取自 `provider_models()["embedding"]`（与检索时同一来源，
    否则会出现「用 A 模型建、用 B 模型查」的静默失效）。
    """
    cfg = load_models_config()
    model = provider_models(cfg)["embedding"]
    if not model:
        raise RagEmbedFailed("config/models.yaml 未配置 embedding 模型名")
    stamp = built_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return rag_index_tool.build_index(
        corpus_dir, out_dir, embed_fn or embed_texts, model=model, built_at=stamp,
        chunk_chars=chunk_chars, chunk_overlap=chunk_overlap)


__all__ = ["INDEX_ROOT", "IndexCorruptError", "IndexMissingError", "RagEmbedFailed",
           "RagIndexError", "embed_texts", "rebuild", "search"]
