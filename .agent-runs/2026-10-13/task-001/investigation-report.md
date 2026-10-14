# 开工前调查报告 —— 数学建模竞赛协作 Agent

- 调查员：Vera（只读）
- 日期：2026-10-13
- 探测脚本：`.agent-runs/2026-10-13/task-001/probe_attachments.py`（可复跑）

---

## 1. 调查目标

为 H:\mm-agent「数学建模竞赛协作Agent」开工前摸底：
(a) 课件参考代码中可复用的 Ollama / LangGraph / 单Agent+工具 模式；
(b) 实测 A题附件1~6 (.xls) 的真实结构，确认读取方案可行。

---

## 2. 关键发现（按重要性排序）

### 2.1 课件里【没有】Ollama 本地调用示例，但有可直接改用的等价封装

- 全库 grep `langchain_ollama|ChatOllama|localhost:11434` → **0 命中**。课件 LLM 一律走两种写法：
  1. **OpenAI 兼容模式**（推荐借鉴）：`langchain_openai.ChatOpenAI(api_key=..., base_url=..., model=...)`
     —— 证据：`08_LangChain核心组件_Agent的调用方式/demo01_Agent的调用方式.py:25-29`
     ```python
     api_key = os.getenv("DASHSCOPE_API_KEY")
     base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
     model = "qwen-plus"
     llm = ChatOpenAI(api_key=api_key, base_url=base_url, model=model, temperature=0, streaming=True)
     ```
     **可借鉴点**：Ollama 本身提供 OpenAI 兼容端点，把 `base_url` 换成 `http://localhost:11434/v1`、`api_key="ollama"`（占位即可）、`model` 填 ollama 模型名即可复用该封装，`.bind_tools()` 等行为与课件后续代码完全兼容。
  2. `langchain_community.chat_models.ChatTongyi(model="qwen-plus")` —— 10/11 号目录全部用它，属阿里云专用，不可用于本地。
- 另有 `LangChain108/models.py` 的多 provider 工厂函数 `get_llm(provider, model, base_url)`（第 83-110 行），可借鉴其「统一入口 + base_url 参数化」设计。
- **本机 Ollama 实测在线**（`curl localhost:11434/api/tags`，2026-10-13 实测），已装模型：
  | 模型名 | 规模 | 能力 |
  |---|---|---|
  | `qwen3.5:latest` | 9.7B | vision, completion, **tools**, thinking |
  | `qwen3.5:0.8b` | 0.87B | vision, completion, tools, thinking |
  | `deepseek-r1:8b` | 8.2B | completion, thinking（**无 tools**） |
  | `gemma3:latest` | 4.3B | completion（无 tools） |
  | `qwen3-embedding:latest` | 7.6B | embedding |
  | `xitao/bge-reranker-v2-m3:latest` / `pdurugyan/qwen3-reranker-0.6b-q8_0` | 0.6B 级 | rerank/embedding |
  → **工具调用型 Agent 主力模型应选 `qwen3.5:latest`**（唯一够大的 tools 能力模型）；embedding/rerank 也有现货，利好后续 RAG。

### 2.2 LangGraph「StateGraph + interrupt + SqliteSaver」最小示例完整存在

三个文件构成递进关系，均在 `10_LangGraph入门/`：

**(a) 带工具调用的最小 Agent 图** — `demo08_带工具调用的LangGraph.py`（136 行）
骨架：`State(TypedDict){messages: Annotated[list, add_messages]}` → `agent_node`（`bind_tools` 后 invoke）→ `tool_node`（手动遍历 `tool_calls`，包 `ToolMessage`）→ `should_continue` 条件路由 → `builder.add_conditional_edges("agent", should_continue, {"tools":"tools", END:END})` + `add_edge("tools","agent")` 回环 → `compile(checkpointer=MemorySaver())`，调用时 `config={"configurable":{"thread_id":...}}`。
可借鉴点：数模 Agent 的「思考→执行工具→回环」主循环可直接套用；`draw_mermaid()` 可视化（demo08:99）。

**(b) interrupt() 人工卡点** — `demo10_人机回环_键盘输入.py`（315 行）
核心片段（demo10:114-146）：
```python
def sensitive_tool_node(state: State):
    ...
    human_decision = interrupt({          # 图在此暂停，状态由 checkpointer 保存
        "action": "delete_database",
        "target": db_name,
        "warning": "此操作不可恢复！"
    })
    if human_decision == "approve":
        result = tools_map["delete_database"].invoke(delete_call["args"])
    ...
# 恢复执行（demo10:256-259）：
result2_resume = graph.invoke(Command(resume=confirm), config=config2)
```
可借鉴点：敏感操作单独拆一个 `sensitive_tool_node` + 路由函数按工具名分流（demo10:148-168），正是数模场景「生成代码前人工审核 / 覆盖文件前确认」的模板。注意 resume 后防重入判断：`if isinstance(last_message, ToolMessage): return {"messages": []}`（demo10:119）。

**(c) SqliteSaver 持久化** — `demo11_存储到SQLite.py`（226 行）
核心片段（demo11:24-31）：
```python
from langgraph.checkpoint.sqlite import SqliteSaver   # pip install langgraph-checkpoint-sqlite
import sqlite3
conn = sqlite3.connect("checkpoints.sqlite", check_same_thread=False)
memory = SqliteSaver(conn)
graph = builder.compile(checkpointer=memory)
```
可借鉴点：interrupt 暂停 → 进程重启 → 用同一 `thread_id` + `Command(resume=...)` 从 SQLite 恢复（demo11:195-205），满足数模竞赛「长跑任务中断续跑」。读取快照：`memory.get(config)["channel_values"]["messages"]`。

### 2.3 「单Agent+工具」与「七种架构」现状

- `08_.../demo01_Agent的调用方式.py`：`langchain.agents.create_agent(model, tools, system_prompt, middleware=[@before_model/@after_model])` 一行建 Agent（第 79-84 行），`agent.invoke({"messages":[("human",...)]})` 调用。**最省事的单Agent+工具骨架**。
- `11_Agent的七种架构/` 实际**只有 3 个 demo，不是 7 个**：demo01 单Agent（手写一轮 tool_call 循环，88 行）、demo02 ReAct（`create_agent` + MemorySaver + 流式输出，157 行）、demo03 Plan&Execute（LLM 先出 JSON 计划再逐步执行，194 行）。**没有 Graph/Workflow「7号架构」代码**。
- `1012_Rag-Agent-mysql-部署&LangGraph入门/` **无任何代码文件**，只有一个未下载完成的百度云视频（`01_项目开发流程讲解.mp4.baiduyun.p.downloading`）。RAG+MySQL 部署部分无码可抄。
- `10_LangGraph入门/checkpoints.sqlite` 是课件运行残留的示例检查点库，可参考但非代码。

### 2.4 附件1~6 全部可被 xlrd 2.0.2 正常读取（实测）

环境：`C:\Users\Lenovo\.workbuddy\binaries\python\versions\3.13.12\python.exe`，已 `pip install --user xlrd==2.0.2 pandas==3.0.6`。6 个文件 `xlrd.open_workbook(formatting_info=True)` 全部 OK，`pd.read_excel(engine="xlrd")` 亦全部 OK。

| 文件 | sheet | 有效sheet尺寸 | 合并单元格 | 空白占比 | 结构要点 |
|---|---|---|---|---|---|
| 附件1 客户满意度综合得分 | `sheet1`（注意小写） | 162×25 | 2（标题行） | 45.4%（13列后全空） | 行1标题；行2表头「月份门店」+1~12月；数据行 A1…共约160门店；**第13列有预计算的年度均值**（如 99.783） |
| 附件2 服务态度得分 | Sheet1（Sheet2/3 为空 0×0） | 160×26 | 1 | 5.3% | 每月占2列：**得分+「合格/不合格」评级交替**；表头 1,'' ,2,''…需跳列解析 |
| 附件3 车辆清洁得分 | Sheet1 | 159×25 | 1 | 0.9% | 同附件2 的「得分+合格」双列结构 |
| 附件4 客户投诉量 | Sheet1 | 159×13 | 1 | **76.2%** | 标题明示「**空格处为0**」→ 缺失即0，非缺失值问题 |
| 附件5 公司发现问题数量 | Sheet1 | 159×13 | 1 | **85.4%** | 同上「空格处为0」 |
| 附件6 月利润定额及实际利润（万元） | Sheet1 | 160×37 | **14** | 1.2% | 双层表头：行2=月份（每月3列，合并单元格跨「利润定额/实际利润」子列），行3=子表头「利润定额/实际利润/空」；行2 还出现 7月 重复值（合并边界），解析需按 merged_cells 还原 |

共同模式：第1行是大标题（合并），第2行起才是真表头 → pandas 需 `header=None, skiprows=1`（附件6 需 skiprows=1 后再处理双层表头）。门店编号 A1、A2… 在第1列。

### 2.5 A题目录全部文件清单（`C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题`）

- 赛题与论文：`汽车租赁服务公司的奖励政策研究-A题.docx`（28KB，**赛题本体**）、`数学建模论文终稿.docx`、`数学建模论文2(1)(1).docx`、`问题2.docx`、`问题4(2).docx`、`附件2--2022年全国大学生数学建模竞赛论文格式.doc`
- 数据：附件1~6 共 6 个 .xls（见上表）、`总表.xlsx`（129KB）、`预处理-有资格月度.xlsx`、`预处理2-排名及其获奖名单.xlsx`
- 旧代码：`untitled2.m`（318B）、`matlab客户满意度综合得分.mat`、`matlab确定月度评比资格.mat`（MATLAB 产物，可用 scipy.io 读）
- 其他：`新建 MindMaster.Document.emmx`（思维导图）

---

## 3. 可选方案（供队长决策）

**方案 A：OpenAI 兼容模式直连本地 Ollama（推荐）**
`ChatOpenAI(base_url="http://localhost:11434/v1", api_key="ollama", model="qwen3.5:latest")` + `create_agent`（08号目录骨架）+ SqliteSaver/interrupt（demo10/11）。
代价：最小改动，课件模式 100% 复用；风险是 9.7B 本地模型工具调用稳定性需实测，必要时降级为手写 tool 循环（demo08 模式，对模型要求更低）。

**方案 B：双模型分工**
主 Agent 用 `qwen3.5:latest`（tools），embedding/rerank 用本机 `qwen3-embedding` + `bge-reranker-v2-m3` 做 RAG（读赛题 docx/论文），全部离线。代价：RAG 部分课件无代码（1012 目录为空），需自建，工作量+1。

**方案 C：数据层统一用 pandas+xlrd 预处理成 tidy 长表**
6 个附件按「门店×月份×指标」熔成长表存 SQLite/CSV，Agent 工具直接查库；附件4/5 空值填0、附件6 双层表头按 merged_cells 展开。代价：一次性预处理脚本约 50-80 行，之后所有分析工具受益。

## 4. 遗留问题

1. 「Graph/Workflow 7号架构」与 RAG-MySQL 部署在课件中**无对应代码**（11号目录仅3个demo、1012目录仅有未下载完的视频），若任务必需需另找来源或自建。
2. `qwen3.5:latest` 的 `thinking` 默认开启，与 LangChain 工具调用链的兼容性（think 标签混入输出）未实测，建议开工后先用最小 demo 验证一轮 tool_call。
3. `总表.xlsx` 与两个「预处理」xlsx 可能是前人（论文终稿）的中间结果，是否与附件口径一致未核对；如需复用建议另行比对。
4. 用户机器上另有 `H:\deepseekHarness` 目录，与本次调查范围无关，未展开。
