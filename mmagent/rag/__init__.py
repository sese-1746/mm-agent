"""RAG 适配层：本地向量索引的唯一入口（第 36 轮启用）。

对外只有 `adapter.search(query, k)`；索引的构建与格式在 `tools/rag_index_tool.py`，
索引产物落 `artifacts/rag/index/`（生成物，不入库，可 `cli rag rebuild` 完整复现）。
"""
