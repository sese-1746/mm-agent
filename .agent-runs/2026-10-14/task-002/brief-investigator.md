# brief-investigator.md — 调查员 Vera（task-002）

- **role_name**: 调查员 Vera
- **mission**: 为人机回环改造（LangGraph `interrupt()` + `SqliteSaver`）做开工前调查，回答 §任务 的 6 个问题，全部结论须有**实测证据**（命令 + 真实输出 + 文件路径）。
- **allowed_edit_scope**: **只读**。禁止修改 `H:\mm-agent` 下任何源码/配置/测试/产物。只允许写你自己的输出文件（见 output_path）与临时探针脚本（放 `.agent-runs/2026-10-14/task-002/probe/`）。
- **output_path**: `H:\mm-agent\.agent-runs\2026-10-14\task-002\investigation-report.md`
- **success_criteria**: 6 个问题逐条给出「结论 + 证据（路径/行号/命令/输出片段）」，无"可能/大概"；不可判定项明确标 UNKNOWN 并说明卡在哪。

---

## 背景

- 项目：`H:\mm-agent`，场景一「数学建模竞赛协作 Agent」。全本地 Ollama + SQLite + FastAPI。
- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`（Python 3.13.12）
- 现状：`mmagent/agents/coordinator.py`（131 行）是**纯 Python 状态机**，三个人工卡点（INTAKE 后口径冲突 / SOLVE 后 sanity blocking / ASSEMBLE 后导出前合规确认）触发时抛 `HumanGateRequired` 并把 stage 置 `HALTED`，**暂停后无法恢复**。
- 文档主架构要求：课件 **7 号 Graph/Workflow** —— `StateGraph` + `interrupt()` + `SqliteSaver` 检查点。
- 用户已裁定：**引入 langgraph**（不自研）。

---

## 必须回答的 6 个问题

### Q1 课件骨架提取
浏览 `C:\Users\Lenovo\Desktop\课件\agent\代码\10_LangGraph入门\`（重点 `demo09_人机回环_程序完成人工的确认动作.py`、`demo10_人机回环_键盘输入.py`、`demo11_存储到SQLite.py`、`demo12_测试持久化存储.py`、`checkpoints.sqlite`）与 `1012_Rag-Agent-mysql-部署&LangGraph入门\`。
给出：(a) `StateGraph` + state 定义写法；(b) `interrupt()` 在哪里调用、返回值怎么用；(c) 恢复方式（是 `Command(resume=...)` 还是 `NodeInterrupt` 还是别的）；(d) `SqliteSaver` 构造与 `thread_id` / `configurable` 约定；(e) 每个发现给**文件路径 + 关键代码片段（≤30 行）**。若多个 demo 写法不一致，指出差异。

### Q2 langgraph 在本机是否真能跑（最高优先级，必须实测）
- 查 `langgraph` 与 `langgraph-checkpoint-sqlite` 是否有 **cp313** 兼容版本；给出**实际最新版本号**。
- **实测安装**（装进 `H:\mm-agent\.venv`，仅此 env，禁止全局 pip）：`H:\mm-agent\.venv\Scripts\python.exe -m pip install langgraph langgraph-checkpoint-sqlite`
- **实测跑通**：把课件 demo11（SqliteSaver 持久化）与 demo09/10（人机回环）**实际执行一遍**，贴出真实输出（成功或完整报错）。
- 给出 `interrupt()` 在该版本的**准确语义**（是否需要 `Command(resume=)`、恢复时传入什么），并用**代码实验**证明，不要凭文档记忆。
- 明确记录：安装后 `pip freeze` 里 langgraph 相关包及其依赖的**精确版本**，供 Max 写入 `requirements.txt`。

### Q3 现有调用点清单
全仓库 grep（排除 `.venv`）：`coordinator`、`HumanGateRequired`、`CoordinatorState`、`from ..agents`、`Stage`。
列出**每一个 import / 调用点**：`文件:行号` + 用途 + 迁移影响面。重点覆盖 `mmagent/app.py`、`mmagent/cli.py`、`mmagent/tools/*`、`mmagent/agents/__init__.py`、`tests/*`。

### Q4 需求原文（验收依据）
在 `H:\mm-agent\.agent-runs\2026-10-13\task-001\docx_dump.txt` 中检索并原文摘录：
- NFR-5（含「丢失版本 ≤1」的确切表述）
- 「人机回环 / 人工卡点 / 暂停恢复 / 检查点」相关的功能需求原文
- 若有关于三个卡点具体确认内容的描述，一并摘录（确认什么、确认后写入什么）
每条给出行号或可定位片段。

### Q5 冻结基线影响面
task-001 冻结基线含 8 个文件（`mmagent/pipeline/intake.py`、`config/field_aliases.yaml`、`README.md`、`mmagent/store/schema.sql`、`mmagent/pipeline/guard.py`、`mmagent/store/db.py`、`mmagent/checks/sanity.py`、`config/intake_scope.yaml`，md5 见 `PROGRESS.md §1`）。
判断本轮改造会波及哪些，特别是：**改 `README.md` 是否必要**、**是否真的需要动 `schema.sql`**（langgraph 检查点能否落在独立 sqlite 文件从而完全不碰它）。给出你的建议与依据。

### Q6 检查点库落位建议
结合 `mmagent/store/db.py`（注意：`connect()` 已设 `PRAGMA foreign_keys / busy_timeout / recursive_triggers`，`init_db` 设 `journal_mode=WAL`；每次 connect 的开销 Tess 已测过）、`mmagent/store/schema.sql` 的 `events` 表，给出：
- 检查点应放哪个文件（`data/mm.db` 还是独立 `data/checkpoints.sqlite`）？理由？
- 检查点表与现有 `events` 留痕表的**职责边界**建议。
- `thread_id` 用什么值（与 `profile_id` / `trace_id` 的关系）？

---

## 交付要求

- 报告写入 `output_path`，结构按 Q1~Q6 分节，开头先给「一句话结论 + 可行性判定（可行 / 不可行 / 有条件可行）」。
- 每条结论后紧跟证据：**文件路径:行号** 或 **命令 + 真实输出片段**。
- 探针脚本放 `.agent-runs/2026-10-14/task-002/probe/`，命名带 `probe_` 前缀，输出同时存一份 `*.txt` dump。
- 完成后回传**简短状态块**：可行性判定、langgraph 实测版本、三个卡点恢复方式的结论、发现的阻塞点、报告路径。**不要把报告全文倒回主会话。**
- 若 Q2 实测失败（例如无 cp313 wheel 或 `interrupt()` 跑不通），**立即回报队长**，不要自行改方案。
