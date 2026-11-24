# 数学建模协作工作台

面向数学建模任务的本地 Web 工作台。用户提交题面和附件，在同一界面整理任务、生成建模方案、检查求解和论文产物，并跟踪项目状态。概念问答提供独立的本地 RAG 入口。

## 工作流

| 环节 | 输入与输出 |
| --- | --- |
| 资料准备 | 题面与附件 → 任务规格、字段和附件检查 |
| 建模 | 任务规格 → 候选方案、人工确认、求解脚本与结果 |
| 交付 | 求解结果 → 论文骨架、一致性检查、可导出文档与项目归档 |
| 知识库问答 | 问题 → 向量检索 → 带来源片段的模型回答 |

业务节点由 Python 和 LangGraph 编排，前端负责操作与状态展示。此项目可用于展示代码式工作流和 RAG 实现。

## 本地运行

使用 Python 3.13。安装依赖后，把 `config/models.yaml` 中的 `provider` 改为 `ollama`，并确认本机 Ollama 已运行。按配置下载嵌入模型和生成模型。使用 DashScope 时，从环境变量设置 `DASHSCOPE_API_KEY`，并选择账户可用的模型。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
ollama pull qwen3-embedding
ollama pull qwen3.5:latest
python -m mmagent.cli serve
```

浏览器打开 [工作台](http://127.0.0.1:8000/) 或 [概念问答](http://127.0.0.1:8000/#/concept)。

## 复现 RAG 检索

仓库附两篇自编短文，覆盖线性规划和时间序列。使用同一个嵌入模型分别生成语料向量和查询向量。更换嵌入模型后重新构建索引。分块的默认配置为 600 字符、重叠 80 字符。

```powershell
python -m mmagent.cli rag rebuild --corpus examples/rag-corpus --chunk-chars 600 --overlap 80
python -m mmagent.cli rag search "线性规划建模先定义什么？" --k 2 --json
```

在 `概念问答` 页输入同一问题，查看回答和来源片段。遇到问答异常，可以先用 `rag search` 检查检索结果、分数和来源，再检查生成模型配置。可对比调整 `--k` 前后的召回结果。

- [文档分块与向量索引](mmagent/tools/rag_index_tool.py)
- [检索适配层](mmagent/rag/adapter.py)
- [问答接口](mmagent/api/routes_rag_qa.py)
- [问答页面](frontend/assets/views/concept.js)

这是从本地已提交版本整理的作品集快照，附带示例语料。运行生成的数据库和向量索引由使用者在本地重建。
