# courseware-hitl-review.md — 新增 HITL/Graph 课件与 `coordinator.py` 实现一致性复核

- 复核人：调查员 Vera（只读）
- 日期：2026-10-14
- 被测实现：`H:\mm-agent\mmagent\agents\coordinator.py`（**实测 `wc -l` = 500 行**，工作区未提交版本；任务书写的是 487 行，以实测为准）
- 课件：
  - `C:\Users\Lenovo\Desktop\课件\agent\02_LangGraph进阶_Human-in-the-loop.pdf`（24 页）
  - `C:\Users\Lenovo\Desktop\课件\agent\07_Graph_Workflow 架构（有向图工作流，企业级主流）.pdf`（21 页，末页 P21 为空白页）
- 抽取产物：`.agent-runs/2026-10-14/task-002/probe/pdf/`（`hitl.pdf.txt` / `graph07.pdf.txt` / `*.norm.txt` / `images/`）

> **复核基线（重要）**：本复核针对**工作区未提交状态**。`git status --porcelain` 实测：
> ` M README.md` / ` M mmagent/agents/__init__.py` / ` M mmagent/agents/coordinator.py` / ` M mmagent/cli.py` / ` M requirements.txt` / `?? tests/test_nfr5_recovery.py`（均为 Max/Tess 的本轮改动，**非本次调查所改**）。
> 若后续实现再变，本报告的行号需重新对齐。

---

## 0. 一句话结论

**课件与实现的大方向完全一致（`interrupt()` 暂停 + `Command(resume=...)` 恢复 + `SqliteSaver` 独立库 + `thread_id` 隔离 + 不用 `MemorySaver`），但存在 1 条 P0 级语义缺陷：人工裁定只被"记录"、没有被"消费"——实测在合规卡点投 `reject` 后流程仍推进到 `DONE`（执行了 export），在 sanity 卡点投 `abort` 后仍继续 render/assemble；课件示范的正是"裁定决定后续动作"（`if human_decision == "approve": 执行 else: 取消`）。另有 4 条 P1（卡点 payload 缺待审内容、无 HTTP/FastAPI 人工确认入口、升级路径 `record_failure` 未接入图、裁定未回写业务库）与若干 P2。**

---

## 1. 课件规范做法摘录（以 HITL 课件为主）

> 页码 = PDF 实际页；行号 = `probe/pdf/hitl.pdf.txt`（NFKC 归一化版 `hitl.norm.txt` 行号相同，因为 NFKC 不改换行）。

### 1.1 核心机制（P1，原文件行 3-40）

| 要点 | 原文（行号） |
|---|---|
| 定义 | 「⼈机回环（Human-in-the-loop）是指 AI 在执行过程中，**遇到关键决策点时主动暂停，等待人类确认后再继续执行**的机制」（L5-7） |
| 两个键的比喻 | 「**`interrupt()` 是"暂停键"**，**`Command(resume)` 是"播放键"**」（L23-25） |
| 为什么用 `Command(resume)` | 「LangGraph 的设计哲学：状态机。`interrupt()` 把状态机暂停在某个节点，**保存完整上下文**。`Command(resume="...")` 从暂停点继续，**传入外部决策作为该节点的返回值**」（L29-30） |
| 流程图 | `用户 → Agent → 敏感操作？ → ⏸ interrupt() 暂停 → 人工确认 → Command(resume) 继续 / 取消`（L17-18） |

### 1.2 `interrupt()` 的规范特点（P2，行 42-63）

- 「**保存完整状态（包括内存、文件、网络连接）**」→ ⚠️ **见 §7 UNKNOWN-1：这句是课件的不准确表述**，langgraph 只持久化 channel 状态。
- 「**抛出特殊异常，不返回结果**」
- 「**必须通过 `Command(resume)` 恢复**」
- 代码形态（L46-53）：`human_decision = interrupt({...})`，payload 是**业务语义字典**（`action` / `target` / `warning`）。

### 1.3 `Command(resume="...")` 的规范特点（P2-P3，行 64-111）

- 「从 `interrupt()` **断点继续执行**」
- 「**`resume` 的值作为 `interrupt()` 的返回值**」（L109）
- 「**状态完整保留，不丢失上下文**」（L111）
- 官方两种取值示例：`Command(resume="approve")` / `Command(resume="reject")`（L70/L77）

### 1.4 状态持久化 / 检查点（P3，行 112-129）

- 三个 saver：`MemorySaver`（内存，演示）/ `SqliteSaver`（SQLite，本地）/ `RedisSaver`（分布式）
- 能力三条：「**自动保存每个节点的执行状态**」（L125）／「**支持暂停、恢复、回溯**」（L127）／「**多会话隔离（通过 `thread_id`）**」（L129）

### 1.5 关键知识点总结（P18 —— **该表是内嵌图片，pypdf 未抽到文本**，由 `probe/pdf/images/hitl_p18_0.png` 读出）

| 概念 | 作用 | 代码 |
|---|---|---|
| `interrupt()` | 暂停执行，等待外部输入 | `human_decision = interrupt({...})` |
| `Command(resume)` | 恢复执行，传入决策 | `graph.invoke(Command(resume="approve"))` |
| `bind_tools()` | 让模型知道可用工具 | `chat.bind_tools(tools)` |
| `tool_calls` | 模型的工具调用请求 | `message.tool_calls` |
| `ToolMessage` | 工具执行结果 | `ToolMessage(content=..., tool_call_id=...)` |
| `MemorySaver` | 内存状态持久化 | `MemorySaver()` |
| `thread_id` | 会话隔离标识 | `{"configurable":{"thread_id":"xxx"}}` |

### 1.6 七、常见问题（P18，行 1466-1474）—— 四条规范结论

| # | 问题 | 课件答案（原文） |
|---|---|---|
| Q1 | 为什么 ai 消息是空的？ | 「模型决定调用工具时 `content` 为空，工具调用信息在 `tool_calls` 字段里」 |
| Q2 | `interrupt()` 会丢失状态吗？ | 「**不会**。`interrupt()` 会保存完整状态到检查点，**恢复时从断点继续**」 |
| Q3 | 生产环境用什么存储？ | 「`SqliteSaver`（本地）或 `RedisSaver`（分布式），**不要用 `MemorySaver`**」 |
| Q4 | **可以暂停多个节点吗？** | 「**可以**。任何节点都可以调用 `interrupt()`，**每个暂停点独立管理**」 |

### 1.7 六、课后练习（P18，行 1465）—— **唯一的"进阶要求"**

> 「**Web 界面：用 FastAPI 封装，通过 HTTP API 接收人工确认**」

### 1.8 恢复后重入 / 幂等性（散见于 P6 L372-374、P13 L1029-1031、P21 L1676-1677）

课件**没有**用"幂等"这个词（`幂等` 在全文出现 **0 次**），但**用代码示范了重入守卫**：

```python
def sensitive_tool_node(state: State):
    last_message = state["messages"][-1]
    # 如果已经处理过（resume 后重新进入），直接跳过
    if isinstance(last_message, ToolMessage):
        return {"messages": []}
```
注释原文：「**如果已经处理过（resume 后重新进入），直接跳过**」——即课件承认「resume 会重新进入节点」，并要求**在节点开头做已处理判定**。

### 1.9 附录：存储方式对比（P24 —— **同样是内嵌图片**，由 `probe/pdf/images/hitl_p24_0.png` 读出）

| 存储方式 | 导入 | 创建 | 特点 |
|---|---|---|---|
| `MemorySaver` | `langgraph.checkpoint.memory` | `MemorySaver()` | 内存，重启丢失 |
| `SqliteSaver` | `langgraph.checkpoint.sqlite` | **`SqliteSaver.from_conn_string("xxx.db")`** | 本地文件，持久化 |
| `RedisSaver` | `langgraph.checkpoint.redis` | `RedisSaver.from_conn_string("redis://...")` | 分布式，多机共享 |

> ⚠️ 注意：课件推荐的构造方式是 **`SqliteSaver.from_conn_string("xxx.db")`**，而我们用的是 `SqliteSaver(sqlite3.connect(...))`。见 §3 P1-5。

### 1.10 课件**没有**讲的内容（NFKC 归一化后关键词核查，`probe_14_normalize_pdf.py` 输出）

`response_schema` **0** 次、`编辑` / `edit` **0** 次、`update_state` **0** 次、`多轮` **0** 次、`幂等` **0** 次、`get_state_history` **0** 次、`时间旅行` **0** 次、`工具内` **0** 次。

→ 所以：**「编辑状态后重入」「工具内中断」「`response_schema`」「`get_state_history`/时间旅行」这几项课件均未涉及**，我们未做不属于"偏课件"，详见 §4。

---

## 2. 表(a)：课件讲了、我们**已做对**

| # | 课件出处 | 课件要求 | 实现处 file:line | 判定 |
|---|---|---|---|---|
| a1 | P1 L29-30 / P2 L63 / P18 Q2 | `interrupt()` 暂停后**必须**用 `Command(resume)` 恢复 | `coordinator.py:50`（`from langgraph.types import Command, interrupt`）、`:170`（卡点1 interrupt）、`:467`（`self._graph.invoke(Command(resume=decision), ...)`） | ✅ 完全一致 |
| a2 | P3 L109 | `resume` 的值作为 `interrupt()` 的返回值 | `coordinator.py:170-179`：`decision = interrupt({...})` 的返回值直接写入 `decisions` 与 `log`（`f"gate:{GATE_CALIBER}={decision.get('resolution')}"`） | ✅ 一致 |
| a3 | P3 L125-129 | 检查点自动保存 + 多会话隔离（`thread_id`） | `coordinator.py:56`（`DEFAULT_CKPT_PATH = Path("data/checkpoints.db")`）、`:322-323`（`SqliteSaver(self._conn)` + `_build_graph`）、`:327-328`（`{"configurable": {"thread_id": run_id}}`） | ✅ 一致（且用了**独立库**，比课件更进一步） |
| a4 | P18 Q3 | 生产**不要用** `MemorySaver` | 用 `SqliteSaver`：`coordinator.py:48` / `:322` | ✅ 一致 |
| a5 | P6 L372-374（重入守卫） | resume 后节点会重新进入，需做"已处理即跳过" | `coordinator.py:169`（`and not state.get("conflicts_confirmed")`）、`:197`（`and GATE_SANITY not in state.get("decisions", {})`）、`:220`（`and not state.get("compliance_confirmed")`）——三处均以**状态判据**做跳过，代替课件的 `isinstance(last_message, ToolMessage)` | ✅ 一致（做法更结构化，见 c2） |
| a6 | P18 Q4 | 任何节点都可 `interrupt()`，每个暂停点独立管理 | 三个卡点各自独立成节点：`coordinator.py:167/195/218`，边 `gate_caliber→profile`(:254)、`gate_sanity→render`(:258)、`gate_compliance→export`(:261) | ✅ 一致 |
| a7 | P18 Q2 | `interrupt()` 不丢状态，**恢复时从断点继续**（不重跑已完成阶段） | `_read_status()` 从 `get_state().tasks[].interrupts[].value` 读回挂起位（`:357-378`）；阶段节点是纯函数，只写 `stage`/`log`（`:163-235`）。实测 `log` 无重复阶段（见 §3 P0-1 实验输出） | ✅ 一致 |
| a8 | P1 L17-18 | 人工确认的**两条出口**：继续 / 取消 | 裁定契约含否定分支：`GATE_SANITY` 允许 `abort`、`GATE_COMPLIANCE` 允许 `reject`（`:82-83`） | ⚠️ **契约已声明、但行为未生效** → 记入 §3 P0-1 |

---

## 3. 表(b)：课件讲了、我们**缺失 / 做偏**（重点）

### 🔴 P0-1｜裁定"只记录、不消费"：`reject` / `abort` 不改变控制流（实测）

| 项 | 内容 |
|---|---|
| 课件出处 | P1 L17-18（`Command(resume)` → **继续 / 取消**）、P6 L395-399（`if human_decision == "approve": 执行 else: 取消`）——课件的裁定**直接决定执行与否** |
| 我们 | 卡点节点把裁定写入 `state["decisions"]` 后**无条件**返回，边是无条件边 → 裁定对控制流没有任何影响 |
| 实现处 | `coordinator.py:218-231`（`_node_gate_compliance`：无论 decision 是 approve 还是 reject，都返回 `{"compliance_confirmed": True, ...}`）／`:195-207`（`_node_gate_sanity` 同理，不区分 accept/fix_and_rerun/abort）／`:258`／`:261`（`gate_sanity→render`、`gate_compliance→export` 均为无条件边）／`:148-157`（`decisions` 声明）——全仓 grep `decisions` 仅 8 处，**没有任何阶段节点读取它做分支** |
| **实测证据** | 用真实 CLI 跑（产物落在 `.agent-runs/2026-10-14/task-002/probe/hitl_probe/`，未触碰 `data/`）：<br>**① sanity 投 `abort`** → `{"stage":"ASSEMBLE","paused":true,...,"log":[...,"gate:sanity_blocking=abort","stage:render","stage:assemble"]}` —— **abort 后仍执行 render + assemble**<br>**② compliance 投 `reject`** → `{"stage":"DONE","paused":false,"done":true,"log":[...,"gate:pre_export_compliance=reject","stage:export"]}` —— **合规否决后仍执行 export 并置 DONE** |
| 为什么是 P0 | 合规卡点是「人判卡点不省」的最后一关（需求 docx 第 443 行：「导出前校验 `ai_disclosure.docx` 存在，**缺失即 422 阻断**」）。当前行为 = 人工否决被**静默忽略**，导出照常完成。这比"没有卡点"更危险：**留痕显示已否决、产物却已导出** |
| 改法要点 | ① 在 `_node_gate_sanity` / `_node_gate_compliance` 内**读取裁定值**并写入状态（如 `sanity_decision` / `compliance_decision`）；② 把 `gate_sanity→render`、`gate_compliance→export` 改成 `add_conditional_edges`，`abort`/`reject` → `END`（或新增 `halted` 终态节点）；③ 补测试：`resume(reject)` 后 `status().done is False` 且 `events` 有 `hitl.gate_rejected`。**（改动涉及条件边，请队长确认是否本轮做）** |

### 🟠 P1-1｜卡点 payload 缺"待审内容"，人工看不到该审什么

| 项 | 内容 |
|---|---|
| 课件出处 | P2 L49-53：`interrupt({"action": "delete_database", "target": db_name, "warning": "此操作不可恢复！"})` —— payload **带具体操作对象与风险提示**；P5 L291 restates |
| 我们 | payload 只有三个**静态**字段：`{"gate": <常量>, "stage": <常量>, "question": <固定文案>}` |
| 实现处 | `coordinator.py:170-174`（卡点1）／`:198-202`（卡点2）／`:221-225`（卡点3）；`GATE_QUESTION` 是模块级常量字典（`:93-97`），不含任何运行期数据 |
| 具体缺口 | `start(run_id, context)` 的 `context`（`:428-440`）只被抽成 4 个 bool，**口径冲突明细 / sanity 阻断项清单 / 缺失的披露文件路径都没有进入 payload**，人工无法据 `question` 做判断 |
| 改法要点 | 把 `context` 中的待审材料（冲突列表、sanity failures、披露文件缺失项）一并放进 `interrupt()` 的 payload，并以 `events.payload` 留痕；`GATE_QUESTION` 作为兜底文案保留 |

### 🟠 P1-2｜缺 HTTP / FastAPI 人工确认入口（课件课后练习明确要求）

| 项 | 内容 |
|---|---|
| 课件出处 | P18 L1465「六、课后练习：**Web 界面：用 FastAPI 封装，通过 HTTP API 接收人工确认**」 |
| 我们 | 只有 CLI：`cli.py:313-337`（`run start` / `run resume` / `run status`）。**`mmagent/api/` 与 `mmagent/app.py` 完全没有 hitl/coordinator 引用**（实测 grep：`mmagent/api/{routes_compliance,routes_intake,routes_profile,routes_solve}.py` + `app.py` 零命中） |
| 为什么是 P1 | 需求原文的接口表（docx 第 210-216 行）本身有 API 路由形态（含 `409/422` 阻断码），且 `app.py:40-43` 已挂 4 条路由——人工卡点缺 HTTP 入口会让"暂停→确认→恢复"在 Web 侧不可用 |
| 改法要点 | 新增 `mmagent/api/routes_hitl.py`（`POST /api/v1/run/start`、`POST /api/v1/run/{run_id}/resume`、`GET /api/v1/run/{run_id}`），复用 `Coordinator.start/resume/status`，把 `InvalidGateDecision` 映射为 422；`app.py` 挂载一行。**（`app.py`/新增 api 文件，请队长确认范围）** |

### 🟠 P1-3｜升级路径 `record_failure` 未接入图（"连续失败 2 次转人工"在图里没有触发点）

| 项 | 内容 |
|---|---|
| 课件出处 | HITL 课件未讲（属需求侧）：docx 第 246/437/450/657 行「连续失败 2 次即换更小模型或降需求并入档；**仍失败则转人工**」 |
| 我们 | `Coordinator.record_failure()`（`coordinator.py:476-489`）实现完整，但**全仓无生产调用者**——实测 grep 显示只有 `tests/test_nfr5_recovery.py:712/724/726` 调用它；`_build_graph`（`:238-263`）里 10 个节点**没有任何一个**尝试阶段失败 |
| 附带死代码 | `TRANSITIONS`（`:122-130`）**无任何消费者**（`_build_graph` 用硬编码 `add_edge`，不再查转移表），`record_success`（`:491-493`）同样只被测试引用 |
| 影响 | 三条 Graph 卡点可暂停/可恢复 ✅，但"失败升级到人工"这条需求路径在当前架构下**不可达** |
| 改法要点 | 在 `_node_solve`（及后续真实阶段节点）内接入 `record_failure()`，达阈值时转 `HALTED` 终态；或明确在 README 标注"升级路径待接入真实阶段节点"（当前 `README.md:78-79` 只说了骨架待接 LLM，未提这条） |

### 🟡 P1-4｜裁定未回写业务库（需求要求"落库"，我们只落 `events.payload`）

| 项 | 内容 |
|---|---|
| 出处 | 需求 docx 第 109 行：「人的判定**作为该节点的返回值落库**」；docx 第 178 行 DDL 有 `caliber_conflict.resolution` / `resolved_by` / `resolved_at` 与 `field_mapping.confirmed_by` / `confirmed_at` |
| 我们 | 裁定只写进 `events.payload`（`coordinator.py:465-466` 的 `hitl.gate_resumed`），**业务表没有任何回写**；`schema.sql`（冻结基线）也确实没有 `caliber_conflict` / `field_mapping` 表（实测 `mmagent/store/schema.sql` 仅 `profile_doc` / `events` / `artifacts` / `metric_obs` / `desensitize_hit`） |
| 定性 | 本轮骨架可接受，但**"落库"目前只是"落事件流水"**，与需求字面不同；且改 `schema.sql` 会破冻结基线 → **需队长决策**（新增迁移文件 vs 重新发布基线 vs 接受当前形态并在 README 标注） |

### 🟡 P1-5｜`SqliteSaver` 构造方式与课件推荐不同（需说明取舍）

| 项 | 内容 |
|---|---|
| 课件出处 | P24 图片表格（`images/hitl_p24_0.png`）：`SqliteSaver.from_conn_string("xxx.db")` |
| 我们 | `SqliteSaver(sqlite3.connect(...))`：`coordinator.py:266-278`（`_open_ckpt_conn`：`check_same_thread=False` + `busy_timeout=5000` + `journal_mode=WAL`）+ `:322` |
| 差异机理（实测源码） | `SqliteSaver.from_conn_string` 是 `@classmethod @contextmanager`，**yield 完即 `closing()` 关闭连接**（`inspect.getsource` 已确认），内部 `sqlite3.connect(conn_string, check_same_thread=False)`；**不含** `busy_timeout` / `WAL` 设置 |
| 结论 | 我们的做法对"长驻 Coordinator 实例 + 跨进程恢复"更合适（连接长开、带忙等与 WAL），**课件的写法适合"每次调用即开即关"的脚本**。→ **判定为"有意偏离"，非缺陷**，但建议在 `README.md` §7 补一句说明，避免后人照课件改回去。**（此项可降级为 P2，看队长口径）** |

### 🟡 P2-1｜未暴露检查点历史 / 步骤快照（课件在 HITL 只提"回溯"能力名，在 07 号课件明确列为编译注释）

| 项 | 内容 |
|---|---|
| 课件出处 | HITL P3 L127「支持暂停、恢复、**回溯**」（仅提能力，未讲 API）；graph07 P13 L1081-1087 编译注释第 3 条「**调试追踪：可以查看每个步骤的状态快照**」 |
| 我们 | `status()` 只返回 `RunStatus`（`run_id/stage/paused/gate/question/done/log`，`:283-303`、`:357-378`），**未暴露 `get_state_history`**；全仓 `get_state_history` 零命中 |
| 改法要点 | 增加 `Coordinator.history(run_id)` 返回 `[(checkpoint_id, next, stage, log)]`，可直接服务 NFR-5「丢失版本 ≤1」的时间旅行校验（我在 `probe_04b_resume` 实测 `get_state_history` 可用，9 条快照） |

### 🟡 P2-2｜`GATE_CALIBER` 取值域与需求 docx 的 `resolution` 枚举不一致

| 项 | 内容 |
|---|---|
| 需求 docx 第 178 行 | `resolution ... CHECK (resolution IN ('unresolved','use_a','use_b','third_party','human_override'))` |
| 我们 | `coordinator.py:81`：`("resolution", frozenset({"use_mapping", "keep_raw", "human_override"}))` —— 仅 `human_override` 与需求重合 |
| 说明 | 该 DDL 属于 docx 的**规划态**，冻结的 `schema.sql` 里并不存在 `caliber_conflict` 表，所以"以哪个为准"**需队长拍板**（见 §7 UNKNOWN-2） |

---

## 4. 表(c)：课件**没讲**、我们**已做**（超出课件）

| # | 我们做了 | 实现处 file:line | 评价 |
|---|---|---|---|
| c1 | **裁定契约的强校验**：`None` / 空 dict / 非 dict / 缺键 / 越界 / 投错 gate / 线程未挂起，一律抛 `InvalidGateDecision`，**绝不把非法值传给 langgraph** | `coordinator.py:112-118`（异常类）、`:399-424`（`_validate_decision`）、`:455-462`（gate 一致性 + 未挂起校验） | 课件完全没有这层；这是对 langgraph 1.2.15 `resume=None` → `UnboundLocalError` 缺陷的必要防御（`README.md:133-141`） |
| c2 | **用状态判据而非消息类型做重入守卫**（`conflicts_confirmed` / `GATE_SANITY not in decisions` / `compliance_confirmed`） | `coordinator.py:169` / `:197` / `:220` | 课件用 `isinstance(last_message, ToolMessage)`；我们的做法不依赖消息结构，更适配非消息型状态 |
| c3 | **`events` 留痕由 driver 统一写**，节点内零副作用（`hitl.run_started` / `coordinator.transition` / `hitl.gate_paused` / `hitl.run_finished` / `hitl.gate_resumed`） | `coordinator.py:330-355`（`_emit`）、`:380-395`（`_after_invoke`）、`:442` / `:465` | 课件在节点内 `print`（demo11 P21 L1690-1693），**没有留痕设计**；我们的做法直接应对"resume 重跑节点"的重复写问题，且实测"同一 `(run_id, gate)` 的 `hitl.gate_paused` 恰好 1 条"（`tests/test_nfr5_recovery.py:599-618`） |
| c4 | **留痕失败降级不阻断、但绝不静默**（首次失败打 stderr 明确警告 + 修复提示，`_emit_degraded` 去重） | `coordinator.py:320` / `:348-355` | 课件无此考虑 |
| c5 | **台账库未初始化时明确报错 exit 3**（不自动建库、不静默丢事件） | `cli.py:218-221`（`_require_ledger`）、`:228-230` / `:249-251` | 课件无此考虑 |
| c6 | **独立的升级路径与三卡点分离**：`HumanGateRequired` 只用于"连续失败转人工"，明确不复用于 Graph 卡点 | `coordinator.py:100-109`（docstring 写明）、`:476-489` | 课件只有一条 HITL 路径；我们用两种异常语义区分两类人工介入（但见 P1-3：升级路径尚未接入图） |
| c7 | **检查点库独立于业务库**（`data/checkpoints.db`，`data/*.db` 已被 `.gitignore` 覆盖） | `coordinator.py:54-56` | 课件附录只给了 `from_conn_string("xxx.db")`，未讨论与业务库的隔离 |
| c8 | **`thread_id` = 调用方传入的 `run_id`**（稳定、可持久化，跨进程恢复键） | `coordinator.py:311`（docstring）、`:327-328` | 课件用 `user_001/user_002` 这类演示值，未讨论生产命名 |
| c9 | **NFR-5 跨进程恢复的真实证据链**（`os._exit(0)` 硬崩溃 → 新进程恢复） | `tests/test_nfr5_recovery.py:100-107`（子进程硬崩溃入口） | 课件 demo11 的"模拟重启"只重新 `compile`、**未真跨进程**（详见 `investigation-report.md` Q1(e)） |

---

## 5. §3 —— `07_Graph_Workflow 架构` 的要求 vs 我们的差距清单

课件对该架构的定位：P1 L7「把任务拆成多个节点，用"图"来定义执行流程。支持：**顺序、分支、循环、并行、人工介入**」；P2 L48-50 架构特点「稳定可控、可调试」「**企业级生产可用**」。
**⚠️ 关键前提**：该课件**通篇没有 `interrupt()` / `Command` 字样**（NFKC 归一化后实测 `interrupt` 0 次、`Command` 0 次）。它的"人工介入"是 P8 L595-616 的 `human_approval` **占位节点**（直接 `return {"requires_human": True, "api_result": "[人工审核] 已通过"}`），文档自己承认「**当前为简化实现，直接标记为通过。生产环境应接入真实的人工审核系统**」（L610）。所以本节的"人在环"要求以**它文档描述的生产做法**为准，与 HITL 课件互为补充。

| # | 07 号课件要求 | 出处 | 我们的状态 | 差距 / 优先级 |
|---|---|---|---|---|
| g1 | **状态管理**：TypedDict 状态载体，所有字段**必须**给初始值（"确保 TypedDict 的完整性"） | P3 L114-153、P14 L1162-1175 | `CoordinatorGraphState`（`:144-158`）定义完整；`start()` 里 7 个字段全部显式初始化（`:432-440`） | ✅ 无差距 |
| g2 | **Reducer**：`Annotated[list, add_messages]` 自动追加而非覆盖 | P3 L117、P2 L87 | `log: Annotated[list, operator.add]`（`:158`）；`decisions` 用"合并后完整 dict"手工累积（`:148-150`） | ✅ 无差距（用 `operator.add` 替代 `add_messages`，因本项目不用 messages） |
| g3 | **条件分支**：`add_conditional_edges` 按状态路由到不同节点 | P12 L1000-1003、P11 L892-898 | ❌ **完全没有条件边**——`_build_graph` 全是 `add_edge`（`:252-262`），条件写在卡点节点内部（`:239` 注释自述） | 🔴 **与课件的显式分支范式偏离**，且正是 P0-1 的根因。改法：卡点后接 `add_conditional_edges`（见 P0-1） |
| g4 | **循环控制防死循环**：`iteration_count` + 阈值强制结束（简单 3-5 次 / 复杂 10-20 次） | P10 L782-812、P4 L253 | 当前是**静态线性无环图**，本轮不需要 | ✅ 暂不适用；但若 g3 引入条件边（reject→END、fix_and_rerun→回到 solve），**必须同时补迭代上限**，否则会出现课件警告的"条件路由设计缺陷导致的无限循环"（P10 L786） |
| g5 | **检查点/持久化**：编译时传 checkpointer，作用三条 = ①状态持久化（断点恢复）②人工介入（暂停等待外部输入后继续）③调试追踪（查看每步状态快照） | P13 L1081-1087 | ①✅（SqliteSaver + 跨进程）②✅（三卡点）③❌（无 `get_state_history` 出口） | 🟡 P2：补 `history()`（同 §3 P2-1） |
| g6 | **人在环位置**：按意图路由到 `human_approval` 节点；生产做法 = ①通知人工队列（企微/钉钉/邮件）②**工作流进入等待状态直到人工审核完成** ③通过则继续、**拒绝则返回友好提示** | P8 L595-610、P7-8 | 图内位置 ✅（gate 节点独立）；但 ③ **"拒绝则返回"未实现**（= P0-1）；①的**通知渠道不存在**（无 HTTP/Webhook，= P1-2） | 🔴 见 P0-1 / 🟠 P1-2 |
| g7 | **错误处理与降级**："实际项目中应添加**超时重试、错误降级**等机制"；示例用 `try/except` 包 `json.loads` 防工作流崩溃、非法分类回退默认值 | P7 L530、P6 L498-506、L414-417 | 留痕降级 ✅（`:348-355`）；❌ **图 `invoke` 无超时/重试**；✅ 非法裁定已拦（`InvalidGateDecision`）；✅ 非法 `context` 已拦（`:430-431`） | 🟡 P2：为 `invoke` 加超时；`InvalidGateDecision` 覆盖了课件的"fallback"思路 |
| g8 | **可观测**：课件靠 `print` + "查看每个步骤的状态快照" | P13 L1084、P17 L1465 | 我们的 `events` 流水（5 类事件）+ `RunStatus.log` 比课件强；缺 | 🟡 补步骤快照出口（= P2-1） |
| g9 | **可视化流程**（架构特点之一 "可视化流程"） | P2 L44 | ❌ 未提供 `graph.get_graph().draw_mermaid()` 导出 | 🟡 P2：一行即可（我在 `probe_03_hitl_memory` 实测该 API 可用） |
| g10 | **并行** `send()` / fan-out（课件示例本身是串行模拟，明确指出真实并行需 `send()` API） | P16 L1340-1351 | 不适用（三卡点必须串行） | ✅ 暂不适用 |

---

## 6. 证据索引

### 6.1 PDF 出处

| 引用 | 文件 | 页码 | 行号（`probe/pdf/*.txt`） | 原文件 |
|---|---|---|---|---|
| 暂停键/播放键比喻 | `02_LangGraph进阶_Human-in-the-loop.pdf` | P1 | 3-40（L23-25 比喻、L29-30 设计哲学） | `pdf/hitl.pdf.txt` |
| `interrupt()` 三特点 | 同上 | P2 | 42-63（L59 "保存完整状态(包括内存、文件、网络连接)"、L61 "抛出特殊异常"） | 同上 |
| `resume` 值即返回值 | 同上 | P2-P3 | 64-111（L109） | 同上 |
| 检查点三能力 | 同上 | P3 | 112-129（L125/L127/L129） | 同上 |
| 重入守卫代码 | 同上 | P6 / P13 / P21 | L372-374 / L1029-1031 / L1676-1677 | 同上 |
| **关键知识点总结表** | 同上 | **P18** | **图片，无文本** | `pdf/images/hitl_p18_0.png` |
| 课后练习（FastAPI/HTTP） | 同上 | P18 | L1465 | `pdf/hitl.pdf.txt` |
| 常见问题 4 条 | 同上 | P18 | L1466-1474（L1470/L1472/L1474） | 同上 |
| 持久化的核心优势 | 同上 | P23 | L1941-1950 | 同上 |
| **存储方式对比表（`from_conn_string`）** | 同上 | **P24** | **图片，无文本** | `pdf/images/hitl_p24_0.png` |
| 架构定义/能力 | `07_Graph_Workflow 架构（...）.pdf` | P1 | L3-24 | `pdf/graph07.pdf.txt` |
| 核心特性 5 条（循环/人工介入/持久化） | 同上 | P4 | L250-255 | 同上 |
| 超时重试/错误降级 | 同上 | P7 | L530 | 同上 |
| 人工审核节点生产做法 | 同上 | P8 | L595-616（L606-610） | 同上 |
| 循环守卫 `check_loop` | 同上 | P10 | L782-812（L786/L790/L798-801） | 同上 |
| 条件边范式 | 同上 | P11-P12 | L892-898 / L1000-1003 | 同上 |
| 编译注释三作用（含调试追踪） | 同上 | P13 | L1081-1088（L1084） | 同上 |
| 并行需 `send()` | 同上 | P16 | L1340-1351 | 同上 |

### 6.2 实现出处（`H:\mm-agent`）

| 引用 | file:line |
|---|---|
| 卡点1 payload/interrupt | `mmagent/agents/coordinator.py:170-174` |
| 卡点2 payload/interrupt | `mmagent/agents/coordinator.py:198-202` |
| 卡点3 payload/interrupt | `mmagent/agents/coordinator.py:221-225` |
| 裁定契约（gate → 必需键 + 域） | `mmagent/agents/coordinator.py:79-84` |
| `decisions` 写入（无消费者） | `mmagent/agents/coordinator.py:177` / `:204` / `:228` |
| 无条件边（gate → 下一阶段） | `mmagent/agents/coordinator.py:258` / `:261` |
| 图构建（全 `add_edge`） | `mmagent/agents/coordinator.py:238-263` |
| 检查点连接打开 | `mmagent/agents/coordinator.py:266-278` |
| `SqliteSaver` + 编译 | `mmagent/agents/coordinator.py:321-323` |
| `resume()` 校验 + `Command(resume=)` | `mmagent/agents/coordinator.py:399-424` / `:449-468` |
| `_read_status`（读 interrupt） | `mmagent/agents/coordinator.py:357-378` |
| 留痕 5 类事件 | `mmagent/agents/coordinator.py:386` / `:389` / `:393` / `:442` / `:465` |
| 留痕降级 | `mmagent/agents/coordinator.py:348-355` |
| 未接入图的升级路径 | `mmagent/agents/coordinator.py:476-493`（仅 `tests/test_nfr5_recovery.py:712/724/726` 调用） |
| 死代码 `TRANSITIONS` | `mmagent/agents/coordinator.py:122-130` |
| CLI 三子命令 | `mmagent/cli.py:224-279` / 参数定义 `:313-337` |
| 台账未初始化 → exit 3 | `mmagent/cli.py:218-221` |
| 无 HTTP 路由（反证） | `mmagent/api/` + `mmagent/app.py` 对 `hitl/coordinator/gate/interrupt` **零命中** |
| README §7 契约与 pitfall | `README.md:81-141` |
| 跨进程恢复测试 | `tests/test_nfr5_recovery.py:100-107` |

### 6.3 复现方式

```bash
# 1) 抽取 PDF 文本（用 Anaconda 自带 pypdf，不向 .venv 装包）
cd H:/mm-agent/.agent-runs/2026-10-14/task-002/probe
/h/Anaconda/python.exe probe_12_pdf_extract.py      # -> pdf/hitl.pdf.txt, pdf/graph07.pdf.txt
/h/Anaconda/python.exe probe_14_normalize_pdf.py    # -> *.norm.txt（NFKC，修康熙部首假阴性）+ 关键词核查
/h/Anaconda/python.exe probe_13_pdf_images.py       # -> pdf/images/*.png（P18/P24 的表格是图片）

# 2) 实测"裁定不驱动控制流"（产物只落本任务目录，不触碰 data/）
cd H:/mm-agent
P=.agent-runs/2026-10-14/task-002/probe/hitl_probe
.venv/Scripts/python.exe -m mmagent.cli db init --db "$P/ledger.db"
.venv/Scripts/python.exe -m mmagent.cli run start --run-id abort-test --sanity-blocking --db "$P/ledger.db" --ckpt "$P/ckpt.db"
.venv/Scripts/python.exe -m mmagent.cli run resume --run-id abort-test --gate sanity_blocking \
    --decision '{"decision":"abort","note":"人工决定终止"}' --db "$P/ledger.db" --ckpt "$P/ckpt.db"
# → 观察：stage 前进到 ASSEMBLE，log 出现 gate:sanity_blocking=abort 之后仍有 stage:render/stage:assemble
```

### 6.4 方法说明（可复现性 + 一处环境差异）

- **任务书要求"用 Read 工具直接读 PDF"** —— 本会话 `Read` 对这两个 PDF 一律返回 `Cannot display content of binary file`（已重试、也已把 PDF 复制到纯 ASCII 路径 `probe/pdf/hitl.pdf`，同样失败）。故改用 **Anaconda 自带 `pypdf`**（`/h/Anaconda/python.exe`，探查确认 `pypdf True`）抽文本，**未向 `H:\mm-agent\.venv` 安装任何包**。
- **pypdf 的两个坑**（都会造成"课件没讲 X"的**假阴性**，已处理）：
  1. 抽出的中文混入 **康熙部首兼容字符**（如 `⽴`(U+2F74) 代替 `立`），直接 grep `立` 会漏 → 已用 **NFKC 归一化**（`probe_14`，两份文本各修正 761 / 868 个字符）。
  2. **表格被渲染为内嵌图片**（HITL P18 关键知识点表、P24 存储方式对比表）→ 已用 `page.images` 抽出 PNG 并**用多模态阅读**（`probe_13`）。`graph07` P21 是**空白页**（0 文本 0 图片），非抽取失败。

---

## 7. UNKNOWN / 需队长确认

1. **UNKNOWN-1：课件 P2「`interrupt()` 保存完整状态（包括内存、文件、网络连接）」是错误表述** —— langgraph 的 checkpointer 只持久化 **channel 状态**（我在 task-002 实测：`checkpoints`/`writes` 两表存 `checkpoint BLOB` + `value BLOB`），**不保存内存对象、文件句柄、网络连接**；恢复后节点是**从头重跑**（`probe_05` 实测副作用重复）。若后续文档/设计引用这句会产生错误预期。**建议在 `README.md` §7.4 补一句澄清**（目前 README 只讲了"重跑"这一面，没直接否掉"连接会保留"）。**是否值得写入 README，请队长定。**
2. **UNKNOWN-2：`GATE_CALIBER` 取值域该以谁为准？** —— 需求 docx 第 178 行是 `unresolved/use_a/use_b/third_party/human_override`，实现是 `use_mapping/keep_raw/human_override`，而冻结的 `schema.sql` 里根本**没有** `caliber_conflict` 表。三个口径互不相同，我无法判定"正确答案"，**需队长拍板**（对齐 docx / 维持实现 / 先补表）。
3. **UNKNOWN-3：卡点 2 的 `fix_and_rerun` 语义** —— 契约允许该值，但"重新运行 solve"是**回边（循环）**，会触发 §5 g4 的迭代上限问题，且当前图是线性图。是否有必要在图上表达"回退重跑"，**超出只读复核范围**，请队长决策。
4. **UNKNOWN-4：`hm` 与 `07` 课件的"人工介入"是否指同一件事** —— HITL 课件用 `interrupt()`，07 号课件的 `human_approval` 只是占位节点且自我声明"生产环境应接入真实的人工审核系统"。若把 07 号课件的 `human_approval` 节点形态当成验收依据，会与 HITL 课件的 `interrupt()` 规范冲突。**我按"HITL 课件为准、07 号课件的生产描述作为补充要求"处理**，此取舍请队长确认。
5. **UNKNOWN-5：并发同一 `run_id` 的 `resume` 竞态** —— `tests/test_nfr5_recovery.py:346` 有一条 **xfail**（"`resume()` 的『读状态(coordinator.py:456) → invoke(coordinator.py:467)』"）。即实现已知该窗口存在竞态。若 P1-2 引入 HTTP 入口，多客户端同时 `resume` 会暴露该问题。**是否提升优先级取决于是否本轮做 HTTP**，请队长定。
6. **未做**：本轮**未**核对我上一份 `investigation-report.md` 中 §2 的 langgraph 版本/语义结论与本次课件的一致性复核（工作量大且课件无版本标注）；**未**检查 `04_Router+Skill`、`06_Blackboard` 等其它新增 PDF 与本轮任务的相关性（任务书只点名两份）。

---

## 8. 次要观察（不计入差距，供参考）

- `README.md:78-79` 的「`agents/*.py` 为骨架，待后续阶段接入 LLM 与 RAG」**未包含**"升级路径（`record_failure`）与真实阶段节点尚未接入"这一事实 → 建议补一句（关联 P1-3）。
- `coordinator.py:20-21` 自述「7 个阶段节点只推进 `stage` 与写最小状态」，与实测一致；本轮 §3 P0-1 的定性应表述为「**骨架轮的设计遗留**」而非"写错"——但**因合规否决会产生"已否决却已导出"的错误产物**，故仍按 P0 上报。
- `mmagent/agents/prompts/` 下**没有** `coordinator_arbitrate.system.j2`（需求 docx 第 254 行提到该文件），`config/models.yaml:32` 却已有 `coordinator` 角色预算 → 二者不一致，与本轮 HITL 无关，仅记录。
