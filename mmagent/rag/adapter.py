"""RAG 适配器（占位）。

本文件是全仓库**唯一**允许 import RAG_ROOT 的模块，当前未启用。
续作接入点：
1. 在 config/models.yaml 的 models.embedding 指定向量模型（当前 qwen3-embedding）；
2. 在此实现 search()：加载 RAG_ROOT 下的向量索引 → 调 Ollama /api/embed 取查询向量
   → 相似度检索 → 返回 [{"text":..., "source":..., "score":...}]；
3. 其余模块（agents/planner 等）只调用 rag.adapter.search，不得直接触碰 RAG_ROOT。
"""
from __future__ import annotations

# RAG_ROOT = Path(r"C:\path\to\rag\root")  # 启用时取消注释（唯一引用点）


def search(query: str, k: int = 5) -> list[dict]:
    """在本地 RAG 知识库中检索最相关的 k 条片段。

    当前未启用：接入点见本模块文件头注释（向量模型取 models.embedding，
    通过 Ollama /api/embed 生成查询向量后在本地索引内做相似度检索）。
    """
    raise NotImplementedError("RAG 适配层尚未启用（占位）；接入点见模块文件头注释")
