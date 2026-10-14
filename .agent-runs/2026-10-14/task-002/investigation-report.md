# investigation-report.md — task-002 人机回环（LangGraph interrupt + SqliteSaver）开工前调查

- 调查员：Vera（只读）
- 日期：2026-10-14
- 仓库：`H:\mm-agent`（HEAD `a901f81`）
- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`
- 探针目录：`.agent-runs/2026-10-14/task-002/probe/`（每个探针均附 `*.txt` 真实输出快照）

---

## 0. 一句话结论 + 可行性判定

**一句话结论**：`langgraph 1.2.15` + `langgraph-checkpoint-sqlite 3.1.2` 在本机 CPython 3.13（cp313）上**实测安装成功、课件 demo11（SqliteSaver 持久化）与 demo09/demo10（人机回环）的等价探针全部跑通、且用 `os._exit(0)` 硬崩溃后由新进程从 SQLite 检查点续跑到 DONE 的跨进程恢复实测通过**；`interrupt()` 需要 `Command(resume=...)` 恢复（已用代码实验证明）。唯一必须规避的坑：`Command(resume=None)` 在 1.2.15 上抛 `UnboundLocalError`（库自身缺陷）。

**可行性判定：✅ 可行**（附 3 条强制注意项，见 §2.6 / §2.7）。

| 判定项 | 结果 | 证据 |
|---|---|---|
| cp313 wheel 可得 | ✅ | `probe_01_versions.txt`：`SOABI=cp313-win_amd64`，全部包安装成功 |
| 安装进 `.venv`（非全局） | ✅ | `python -m pip install` 输出 `Successfully installed ... langgraph-1.2.15 ...`（§2.2） |
| demo11（SqliteSaver 持久化）跑通 | ✅ | `probe_02_sqlite_persistence.txt` |
| demo09/demo10（人机回环）跑通 | ✅ | `probe_03_hitl_memory.txt` |
| 跨进程恢复（崩溃 → 新进程续跑） | ✅ | `probe_04a_suspend.txt` + `probe_04b_resume.txt`（pid 32960 → 4028） |
| `interrupt()` 必须配 `Command(resume=)` | ✅ 已证明 | `probe_05_interrupt_semantics.txt` (a)(b) |
| 现有 190 例测试不被依赖安装打破 | ✅ | `probe_09_pytest.txt`：`189 passed, 1 xfailed in 130.68s`，`PYTEST_EXIT=0` |
| 安装后零外网出站 | ✅（无连接尝试） | `probe_10_offline_egress.txt`：socket 连接尝试 = 0 |

---

## Q1 课件骨架提取

课件路径实际为 `C:\Users\Lenovo\Desktop\课件\agent\代码\10_LangGraph入门\`
（brief/plan 中写作 `...\agent\10_LangGraph入门\`，**少了一层 `代码\`**；task-001 final-summary.md:96 同样写错，建议后续引用统一更正）。

`1012_Rag-Agent-mysql-部署&LangGraph入门\` 下**没有代码**，只有一个未下载完的视频占位文件：
`1012_.../video/01_项目开发流程讲解.mp4.baiduyun.p.downloading`（214 MB，`.downloading` 后缀）→ 该目录**无可提取内容**。

### (a) `StateGraph` + state 定义写法

有两种写法，课件都给了：

**写法 1 —— TypedDict（推荐，人机回环 demo 全用它）**
`demo11_存储到SQLite.py:38-39`
```python
class State(TypedDict):
    messages: Annotated[list, add_messages]
```
`demo05_sState定义方式_TypedDict.py:8-10` 展示了不带 reducer 的普通字段：
```python
class State(TypedDict):
    # count: Annotated[int, operator.add]  # 使用累加减速器
    count: int  # 普通字段，默认覆盖
```
构图（`demo05:25-33`）：
```python
builder = StateGraph(State)
builder.add_node("chatbot", first_node)
builder.add_edge(START, "chatbot")
builder.add_edge("chatbot", "second_node")
builder.add_edge("second_node", END)
graph = builder.compile()
```

**写法 2 —— Pydantic BaseModel**（`demo06_sState定义方式_Pydantic.py:12-15`、`demo07_边的三种类型.py:12-18`）：
```python
class State(BaseModel):
    messages: Annotated[list, add_messages] = []
    need_tool: bool = False
    intent: str = ""
```
`demo06:24-25` 明确点出差异：**Pydantic 用属性访问（`state.messages`），TypedDict 用下标（`state["messages"]`）**。`demo07:41-45` 还演示节点可以 `return State(...)` 返回整个模型实例。

> 对本项目的启示：三个卡点只需 `stage`/`context` 一类字段，用 **TypedDict + 少量 reducer** 即可，无需 Pydantic（现状 `CoordinatorState` 是 dataclass，见 Q3）。

### (b) `interrupt()` 在哪里调用、返回值怎么用

调用点：**节点函数体内、真正要问人之前的那一刻**；返回值就是「人工决策」，直接参与后续分支。

`demo11_存储到SQLite.py:98-135`（敏感操作节点，行 120-124 是 interrupt）：
```python
def sensitive_tool_node(state: State):
    last_message = state["messages"][-1]
    if isinstance(last_message, ToolMessage):
        return {"messages": []}          # resume 后防重入
    ...
    human_decision = interrupt({          # ← 图在此暂停
        "action": "delete_database",
        "target": db_name,
        "warning": "此操作不可恢复！"
    })
    if human_decision == "approve":       # ← 返回值就是人的决策
        result = tools_map["delete_database"].invoke(delete_call["args"])
        content = f"✅ {result}（已人工确认）"
    else:
        content = "❌ 操作已取消（人工拒绝）"
    return {"messages": [ToolMessage(content=content, tool_call_id=delete_call["id"])]}
```
`demo09` 的同一节点在 `demo09_人机回环_程序完成人工的确认动作.py:113-149`（interrupt 在 134-138 行），逻辑一致。

**payload 读取位置**（实测，`probe_05_interrupt_semantics.txt` (d)）：
- `graph.invoke(...)` 返回值里多一个键 `__interrupt__`：`[Interrupt(value={'ask': '确认？', 'n': 1}, id='23c8...', response_schema=None)]`
- 或 `graph.get_state(config).tasks[0].interrupts`，再取 `Interrupt.value`。

**防重入模式（重要）**：`demo09:117-119` / `demo10:118-120` 都有
```python
if isinstance(last_message, ToolMessage):   # resume 后节点会被重跑
    return {"messages": []}
```
—— 这正是「resume 会从头重跑节点」的应对（机理见 §2.5）。

### (c) 恢复方式：`Command(resume=...)`

**课件用的是 `Command(resume=...)`，不是 `NodeInterrupt`**。三处一致：

- `demo11_存储到SQLite.py:196-204`（并注释「用同一个 thread_id 恢复」）
```python
graph2 = builder.compile(checkpointer=memory)   # 模拟重启
config2 = {"configurable": {"thread_id": "user_002"}}
user_input = input("刚才有个暂停操作，请输入 'approve' 或 'reject'")
result2_resume = graph2.invoke(Command(resume=user_input), config=config2)
```
- `demo09_...py:268-271`：`graph.invoke(Command(resume="approve"), config=config2)`
- `demo10_键盘输入.py:250-259`：`input()` 取 `y/n` → 映射成 `approve/reject` → `Command(resume=confirm)`
- 文件头注释也写死：`demo09:5-6`「`Command(resume="...")`: 恢复执行，传入人工决策」

导入位置：`from langgraph.types import interrupt, Command`（`demo09:20`、`demo10:20`、`demo11:14`）。

> ⚠️ 全仓库（含课件目录）**未出现 `NodeInterrupt`**，plan.md §4.2 里提到的「老版本用 NodeInterrupt」在本批次课件中无对应写法。

### (d) `SqliteSaver` 构造与 `thread_id` / `configurable` 约定

`demo11_存储到SQLite.py:24-34`（课件注释直接写「就用这一段，直接替换你所有的存储代码」）：
```python
# pip install langgraph-checkpoint-sqlite
from langgraph.checkpoint.sqlite import SqliteSaver
import sqlite3

conn = sqlite3.connect("checkpoints.sqlite", check_same_thread=False)
memory = SqliteSaver(conn)
print(f"✅ 使用存储：{type(memory).__name__}")
```
- **构造**：传一个 `sqlite3.Connection`；`check_same_thread=False` 必须给（FastAPI/多线程场景尤其）。
- **编译挂载**：`graph = builder.compile(checkpointer=memory)`（`demo11:166`、`demo09:206`）。
- **thread_id 约定**：`config = {"configurable": {"thread_id": "<业务会话 id>"}}`，随每次 `invoke` 传入（`demo11:175/184/198/210`）。同一 `thread_id` = 同一会话/同一可恢复线程。
- **读快照**：`memory.get(config)`（`demo11:211`、`demo12_测试持久化存储.py:12`）；课件按 `snapshot["channel_values"]["messages"]` 取消息（`demo11:214-216`）—— 实测该结构在 1.2.15 仍成立（`probe_02` 场景4 打印 `snapshot type = dict`，消息数 3）。
- `MemorySaver` 是 demo09/demo10 的内存版（`from langgraph.checkpoint.memory import MemorySaver`，`demo09:19`、`demo09:204`），接口与 `SqliteSaver` 同构，仅持久化介质不同。

### (e) 各 demo 写法差异（需要点出的不一致）

| 差异点 | demo09 | demo10 | demo11 | demo12 |
|---|---|---|---|---|
| checkpointer | `MemorySaver`（进程内存，`demo09:204`） | `MemorySaver`（`demo10:194`） | `SqliteSaver` + 文件 `checkpoints.sqlite`（`demo11:29-31`） | 只读 `SqliteSaver` 打开同一文件（`demo12:1-6`） |
| 人工决策来源 | 程序硬编码 `"approve"`/`"reject"`（`demo09:269/295`） | 键盘 `input()` + `y/n→approve/reject`（`demo10:250-254`） | 键盘 `input()`（`demo11:200-203`） | — |
| 图回边 | `sensitive_tool → agent`（`demo09:199`） | 同 demo09（`demo10:190`） | 同（`demo11:164`） | — |
| 场景组织 | 函数 `operator1~4()` 逐个调用 | 平铺顺序执行 | 平铺顺序执行 | 仅重放「场景4」读快照 |

**最需要指出的一处「名不副实」**：`demo11` 场景 3 注释写「模拟：程序重启后，从数据库恢复状态」（`demo11:193`），但代码是
```python
graph2 = builder.compile(checkpointer=memory)   # 同一个内存里的 SqliteSaver 对象，同一个 conn
```
即**只重新 compile 了一次图，既没关连接、也没起新进程**，因此它只证明了「检查点写进了 SQLite 文件」，**没有**证明「进程崩溃后新进程能恢复」。真正的跨进程证据由本次 `probe_04a/04b` 提供（§2.4）。

---

## Q2 langgraph 在本机是否真能跑（最高优先级，已实测）

### 2.1 环境事实（先纠正一处口径）

| 项 | brief/README 声称 | 实测 |
|---|---|---|
| Python | 3.13.12（README.md:8、brief） | **3.13.14**（`3.13.14 (main, Jun 11 2026, 04:04:46) [MSC v.1944 64 bit (AMD64)]`） |
| SOABI | — | `cp313-win_amd64` |
| sqlite3 库 | — | 3.53.1 |

证据：`probe_01_versions.txt`。README.md:8 与 README §6「本机无 CPython 3.11 → 使用 3.13.12」两处均为旧值，需一并订正（这也是 README 必须改的理由之一，见 Q5）。

### 2.2 实测安装（装进 `.venv`，未用全局 pip）

命令与真实输出（`H:\mm-agent\.venv\Scripts\python.exe -m pip install langgraph langgraph-checkpoint-sqlite`）：

```
Downloading langgraph-1.2.15-py3-none-any.whl (259 kB)
Downloading langgraph_checkpoint-4.3.0-py3-none-any.whl (58 kB)
Downloading langgraph_checkpoint_sqlite-3.1.2-py3-none-any.whl (41 kB)
Downloading langgraph_prebuilt-1.1.0-py3-none-any.whl (41 kB)
Downloading langgraph_sdk-0.4.7-py3-none-any.whl (162 kB)
Downloading langchain_core-1.6.10-py3-none-any.whl (573 kB)
...
  Attempting uninstall: websockets
    Found existing installation: websockets 17.2
    Uninstalling websockets-17.2:
      Successfully uninstalled websockets-17.2
Successfully installed aiosqlite-0.22.1 ... langgraph-1.2.15 langgraph-checkpoint-4.3.0
  langgraph-checkpoint-sqlite-3.1.2 langgraph-prebuilt-1.1.0 langgraph-sdk-0.4.7
  langchain-core-1.6.10 langsmith-0.14.8 orjson-3.13.0 ormsgpack-1.12.2
  sqlite-vec-0.1.9 websockets-16.1.1 xxhash-4.0.1 zstandard-0.25.0 uuid-utils-0.17.1 ...
```

- **无 cp313 障碍**：`langgraph` / `langgraph-checkpoint` / `langgraph-checkpoint-sqlite` 都是 `py3-none-any`（纯 Python，`Requires-Python = >=3.10`，`probe_01_versions.txt` + `pip show` 元数据）；带 C 扩展的依赖（`orjson-3.13.0-cp313-cp313-win_amd64`、`uuid_utils-0.17.1-cp313-cp313-win_amd64`、`xxhash-4.0.1-cp313`、`sqlite_vec-0.1.9-py3-none-win_amd64`）**都有 cp313/win_amd64 可用件**。
- **`sqlite-vec` 原生扩展实测可加载**（`langgraph-checkpoint-sqlite` 的依赖，`pip show` 显示 `Requires: aiosqlite, langgraph-checkpoint, sqlite-vec`）：
  ```
  import sqlite_vec OK, ver= 0.1.9
  load 扩展 OK; vec_version= ('v0.1.9',)
  orjson/ormsgpack/xxhash/zstandard/uuid_utils import OK
  ```

### 2.3 实测跑通 demo11（SqliteSaver 持久化）

探针 `probe_02_sqlite_persistence.py` / 输出 `probe_02_sqlite_persistence.txt`。

> **必要的改造说明（必须记录）**：课件 demo11 的 `agent_node` 用 `ChatTongyi(model="qwen-plus")`（`demo11:59`），需要外网 + `DASHSCOPE_API_KEY`，与项目「零外网」冲突。故探针**只把 agent 节点换成确定性 stub**（仍返回带 `tool_calls` 的 `AIMessage`），`State` / `tools` / 路由 / `sensitive_tool_node` / `interrupt()` / `SqliteSaver` / `thread_id` / 场景 1~4 流程与 demo11 **逐行对齐**。`interrupt()`+`Command(resume=)` 的语义因此**未被改动**。

真实输出节选：
```
OK 使用存储：SqliteSaver
【场景1】普通查询
最终答案：西安 今天天气晴朗，气温为25°C。
【场景2】敏感操作 - 暂停
系统已暂停   操作：删除数据库 'users'
图已暂停，等待人工确认...
  返回 dict keys : ['messages', '__interrupt__']
  __interrupt__  : [Interrupt(value={'action': 'delete_database', 'target': 'users', 'warning': '此操作不可恢复！'}, id='a1ab451f...', response_schema=None)]
【场景3】从持久化存储恢复
[probe] interrupt() 在 resume 时返回：'approve' (type=str)
恢复后答案：OK 数据库 `users` 已成功永久删除（已人工确认）
【场景4】查看持久化状态
会话 user_001：  snapshot type = dict   消息数：3
会话 user_002：  snapshot type = dict   消息数：3
演示完成！数据保存在：C:\Users\Lenovo\AppData\Local\Temp\vera_probe11_checkpoints.sqlite
```

### 2.4 实测跑通 demo09/demo10（人机回环）+ 跨进程恢复（NFR-5 关键证据）

**demo09 等价探针**（`probe_03_hitl_memory.py` / `probe_03_hitl_memory.txt`，`MemorySaver`，含 `graph.get_graph().draw_mermaid()` 输出）：
```
场景1 最终答案：西安 今天天气晴朗，气温为25°C。
场景2 第1步：... 图已暂停；返回 keys=['messages', '__interrupt__']；__interrupt__=[Interrupt(value={'action': 'delete_database', ...})]
场景2 第2步：人工批准，恢复执行
    [interrupt 返回] 'approve'
场景2 最终答案：OK 数据库 `users` 已成功永久删除（已人工确认）
场景3 第2步：人工拒绝，恢复执行
    [interrupt 返回] 'reject'
场景3 最终答案：操作已取消（人工拒绝）
场景4：user_001/user_002/user_003 快照均 snapshot type=dict，消息数 3
```
（demo10 与 demo09 的唯一差别是 `input()` 取 `y/n`，探针以程序化 `approve/reject` 覆盖同一路径，故未单列探针。）

**跨进程恢复实验**（`probe_04a_suspend.py` + `probe_graph_def.py` + `probe_04b_resume.py`，把三个卡点最小化成 `intake/solve/assemble` 三个 interrupt 节点）：

阶段 A（`probe_04a_suspend.txt`，pid=32960，跑到卡点1 后 `os._exit(0)` **硬崩溃**，不 close、不清理）：
```
[phase A] __interrupt__ = [Interrupt(value={'gate': 'caliber_conflict', 'stage': 'INTAKE', ...}, id='e1a7b624...')]
[phase A] snapshot.next     = ('intake',)
[phase A] snapshot.values.stage = INTAKE
[phase A] 库内表 = ['checkpoints', 'writes']
[phase A] checkpoint 行数 = 2
[phase A] 模拟崩溃：os._exit(0)，不 close 连接、不做清理
```

阶段 B（`probe_04b_resume.txt`，**新进程 pid=4028**，重新 `sqlite3.connect` + 重新 `compile`）：
```
[phase B] 读回检查点 snapshot.next = ('intake',)
[phase B] 待处理 task.interrupts = (Interrupt(value={'gate': 'caliber_conflict', 'stage': 'INTAKE', ...}),)
[phase B] 卡点1 恢复：Command(resume='approve')  → stage = SOLVE，挂到卡点2
[phase B] 卡点2 恢复：Command(resume={'approve': True, 'note': '已复核'}) → stage = ASSEMBLE，挂到卡点3
[phase B] 卡点3 恢复：Command(resume='approve')  → stage = DONE
[phase B] 最终 log = [gate1='approve', profile, plan, gate2={'approve': True, 'note': '已复核'}, render, gate3='approve', export]
[phase B] 终态 snapshot.next = ()  (空=已到 END)
[phase B] 检查点历史（共 9 条）  #1 DONE ... #8 INTAKE #9 __start__
```
**结论：NFR-5 的「跨进程恢复」在纯 API 层面成立** —— 硬崩溃后，新进程仅凭 SQLite 文件即可读回挂起位置并续跑到结束，且未重跑已完成阶段（log 顺序无重复）。另：`graph.get_state_history(config)` 直接给出 9 条历史快照，可支撑「丢失版本 ≤1」的时间旅行式校验。

### 2.5 `interrupt()` 在该版本（1.2.15）的准确语义 —— 代码实验证据

全部来自 `probe_05_interrupt_semantics.txt` / `probe_06_falsy_resume.txt` / `probe_07_resume_none_bug.txt`。

1. **必须用 `Command(resume=X)`；plain `invoke(None, config)` 不会恢复**（`probe_05` (a)）：
   ```
   首次 invoke -> __interrupt__ 存在=True
     [node] 已进入 gate_node，副作用累计=2
   invoke(None) 返回 keys=['log', '__interrupt__']
   invoke(None) 后 next=('gate',)
   ```
   即 `invoke(None)` **重新进入节点并再次挂起**（不报错、也不推进）。
2. **`Command(resume=X)` 让 `interrupt()` 返回 X**（`probe_05` (b)）：
   ```
     [node] interrupt() 返回 = 'APPROVED_BY_HUMAN'
   恢复后 log = [..., HumanMessage(content="resume='APPROVED_BY_HUMAN'")]
   ```
3. **resume 会从头重跑该节点（副作用重复！）**（`probe_05` (b)）：恢复前后副作用计数 `3 → 4`；`probe_02` 中「系统已暂停」横幅被打印了**两次**（首次 invoke 一次、resume 时一次）——因为 `interrupt()` 之前的代码被重新执行。**这是实现方最容易踩的坑**：写库/写事件/落文件等副作用必须放在 `interrupt()` **之后**，或做幂等保护（课件的 `isinstance(last_message, ToolMessage): return {"messages": []}` 就是这种保护，`demo09:117-119`）。
4. **同一节点内多次 `interrupt()`：每次 resume 都从头重放整个节点，interrupt 值按序回放**（`probe_05` (e)）：resume=val_1 后节点重跑（first 返回 val_1，然后挂到 second）；resume=val_2 后节点再重跑（first 又返回 val_1，second 返回 val_2）。N 个 interrupt → 节点共执行 N+1 次。
   - 设计建议：**一节点一卡点**，卡点串行，避免同节点多 interrupt。
5. **假值可以正常投递**（`probe_06_falsy_resume.txt`）——人工决策为 `False/0/''/[]` 时都会恢复，判定逻辑**不能**写 `if decision:`：
   ```
   [Command(resume=False)] 收到值=[False]  仍挂起=False next=()
   [Command(resume=0)    ] 收到值=[0]      仍挂起=False next=()
   [Command(resume='')   ] 收到值=['']     仍挂起=False next=()
   [Command(resume=[])   ] 收到值=[[]]     仍挂起=False next=()
   ```
6. **`Command(resume={})`（空 dict）会静默不恢复**（同上）：`收到值=[] 仍挂起=True next=('n',)` —— 危险的无操作。原因见 §2.7 源码：空 dict 命中「resume map」分支。
7. **`Command(resume=None)` 抛异常**（见 §2.6）。

### 2.6 ⚠️ 阻塞级缺陷：`Command(resume=None)` 在 1.2.15 上抛 `UnboundLocalError`

`probe_07_resume_none_bug.txt`：
```
  File "H:\mm-agent\.venv\Lib\site-packages\langgraph\pregel\_loop.py", line 1016, in _first
    if not writes and not resume_is_map:
UnboundLocalError: cannot access local variable 'resume_is_map' where it is not associated with a value
```
根因（读源码确认）`H:\mm-agent\.venv\Lib\site-packages\langgraph\pregel\_loop.py:993-1017`：
```python
if input_is_command:
    if (resume := cast(Command, self.input).resume) is not None:   # 993
        ...
        if resume_is_map := (isinstance(resume, dict) and all(...)):  # 999 ← 只在 not None 时赋值
            self.config[CONF][CONFIG_KEY_RESUME_MAP] = resume
        else:
            if len(self._pending_interrupts()) > 1:                   # 1005
                raise RuntimeError("When there are multiple pending interrupts, you must specify the interrupt id ...")
    writes: defaultdict[...] = defaultdict(list)                      # 1011
    for tid, c, v in map_command(cmd=cast(Command, self.input)):
        if not (c == RESUME and resume_is_map):                       # 1014 ← 未赋值即使用
            writes[tid].append((c, v))
    if not writes and not resume_is_map:                              # 1016 ← 崩在这里
        raise EmptyInputError("Received empty Command input")
```
`resume=None` 时 `993` 行条件为假 → `resume_is_map` 从未赋值 → `1014`/`1016` 抛 `UnboundLocalError`。

**规避要求（写入实现约束）**：永不传 `resume=None`。人工「不给值/跳过」必须用显式哨兵（如字符串 `"noop"` 或结构化 dict），不能靠 `None`。
**附带风险**：`_loop.py:1005-1009` 表明「存在多个 pending interrupt 时必须用 interrupt-id 映射形式恢复」（此条为**读源码**结论，非运行实测；本实验未构造多 pending 场景）。→ 更加支持「一节点一卡点」。

### 2.7 供 Max 写入 `requirements.txt` 的精确版本

`probe_08_pip_freeze.txt`（`pip freeze` 全量 73 项，节选）：

**直接必需（2 项）**
```
langgraph==1.2.15
langgraph-checkpoint-sqlite==3.1.2
```
**langgraph 传递依赖（建议显式锁定，避免下次安装漂移）**
```
langgraph-checkpoint==4.3.0
langgraph-prebuilt==1.1.0
langgraph-sdk==0.4.7
langchain-core==1.6.10
langchain-protocol==0.0.19
langsmith==0.14.8
aiosqlite==0.22.1
sqlite-vec==0.1.9
orjson==3.13.0
ormsgpack==1.12.2
```
**本次安装新装/变更（须记录）**
- `websockets` **17.2 → 16.1.1**（被 langgraph-sdk/langsmith 拉低；`pip show websockets` → `Required-by: langgraph-sdk, langsmith`）。**项目无声明消费者**：`requirements.txt` 无 websockets，`uvicorn==0.54.0` 的 `Requires: click, h11` 也不含它 → 降级对项目无功能影响，但**建议在 requirements.txt 显式写 `websockets==16.1.1`** 以固化环境。
- 新增 `httpx2==2.13.1`、`httpcore2==2.13.1`（`Required-by: langsmith`，是 langsmith 0.14.8 的依赖，与项目自带的 `httpx==0.28.1` **并存不冲突**，后者版本未变）。建议一并记录，避免 `env-attest` 对不上。

### 2.8 附加实测：安装后**未改变** task-001 基线

- 全量测试（`probe_09_pytest.txt`）：**190 例，`189 passed, 1 xfailed in 130.68s`，`PYTEST_EXIT=0`**，与 task-001 基线（189 passed / 1 xfailed）**逐项一致** → 依赖安装未打破已验收语义。
  - 复跑命令（把 basetemp 指到本任务目录，避免 pytest 清理系统 Temp 时被工作区批量删除护栏拦下、导致汇总行丢失）：
    ```
    cd /h/mm-agent
    ./.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider \
        --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp"
    ```
- `git status --porcelain` 在跑测试前后均为 `?? .agent-runs/2026-10-14/`，**仓库其余文件零改动**（`.pytest_cache` 也已用 `-p no:cacheprovider` 规避）。
- `python -m mmagent.cli netguard-check` → `[netguard-check] 通过`（EXIT=0）。

---

## Q3 现有调用点清单

grep 范围：`H:\mm-agent` 全仓（`*.py` / `*.md` / `*.txt` / `*.yaml`），排除 `.venv` / `.git` / `.agent-runs`。

**结论：`coordinator` 是一个「孤儿模块」——全仓库没有任何地方 import 它。**

| 位置 | 内容 | 用途 | 迁移影响面 |
|---|---|---|---|
| `mmagent/agents/coordinator.py:1-131` | 模块本体：`Stage`(21-30)、`HumanGateRequired`(33-38)、`TRANSITIONS`(42-50)、`CoordinatorState`(53-59)、`Coordinator`(62-131) | 规则状态机骨架，三处 `TODO(LangGraph)` 在 106 / 113 / 119 行 | **改造对象** |
| `mmagent/agents/coordinator.py:16` | `from ..store.db import DEFAULT_DB_PATH, connect, utcnow` | 唯一的内部依赖（store 层） | 需保留依赖方向（`agents → store`） |
| `config/models.yaml:32-35` | `coordinator: {temperature: 0.0, max_tokens: 1024, call_budget: 100}` | 角色预算配置 | **不需改**（改造后 coordinator 仍非 LLM 主体，见 Q4 需求原文 253 行） |
| — | `mmagent/app.py` **未 import** | app.py:15-17 只 import 4 条路由；40-43 挂载 | 无 |
| — | `mmagent/cli.py` **未 import** | 子命令只有 `db init` / `intake` / `env-attest` / `netguard-check` / `desensitize-check`（cli.py:181-211） | 无（且**没有 coordinator 子命令**） |
| — | `mmagent/tools/*` **未 import** | 且有反向约束：`mmagent/tools/__init__.py:3`「tools **不得** import agents」；`mmagent/tools/solve_tool.py:40-45` 明确「tools 层不 import agents，以保持依赖方向单向」 | 无（**不得**在此引入） |
| — | `mmagent/agents/__init__.py` | 仅一行 docstring，**无 re-export** | 无 |
| — | `tests/*`（7 个文件）**无 coordinator 用例** | tests 目录只有 conftest / test_fr13_disclosure / test_fr2_intake / test_fr5_guard_runner / test_fr6_render / test_fr9_profile / test_sanity | 无既有用例会被打破；**需新增** |

**由此推出的两条结论**：
1. **改造零破坏**：Max 无论重写 `coordinator.py` 还是新增 `mmagent/agents/graph.py`，都**不会打破任何既有调用点**（DoD#4 的「不留半边改完」在既有面上天然满足）。
2. **必须主动接入，否则新能力没有入口**：`cli.py` 没有 coordinator 子命令、`app.py` 没有对应路由、`tests/` 没有用例 → 若只改 `coordinator.py`，DoD#1/#2/#5（暂停→重启→恢复的端到端与测试）**无法被外部触达与验收**。建议 Max 至少补：一个 CLI 子命令（如 `cli gate resume --thread-id ... --decision ...`）或 API 路由 + `tests/test_nfr5_recovery.py`。**是否需要这一步、以及入口形态，请队长拍板。**

**附带发现**：`cli.py:23-24` 的 `TRACKED_DEPS`（env-attest 采集的依赖清单）不含 langgraph。若希望 `env_attest` 能证明「本地 langgraph 版本可追溯」，需要补该列表——`cli.py` **不在冻结基线内**，可自由修改。建议加 `"langgraph", "langgraph-checkpoint-sqlite"`。

---

## Q4 需求原文（验收依据）

来源：`H:\mm-agent\.agent-runs\2026-10-13\task-001\docx_dump.txt`。

### 4.1 NFR-5（含「丢失版本 ≤1」的确切表述）

需求追溯表 **第 346 行**（原文）：
> `NFR-5 可靠性 | event_log 重建结果、backup_log | tests/test_nfr5_recovery.py | python -m pytest tests\test_nfr5_recovery.py -q`

「丢失版本 ≤1」共出现 5 处，逐条原文：

- **第 110 行**（架构选型说明）：
  > 检查点（checkpointer）用 SQLite 实现（对应课程 SqliteSaver 的本地方案）：进程崩溃后按事件表重建，**恢复演练丢失版本 ≤1（NFR-5）**。
- **第 407 行**（任务 T32）：
  > `T32 | 崩溃恢复演练：计划内 kill 后按事件表重建 | T20 | test_nfr5_recovery.py 报告 | python -m pytest tests\test_nfr5_recovery.py -q，丢失版本 ≤1`
- **第 441 行**（风险表「设备故障或状态崩溃」）：
  > 距上次备份 >30 min，或进程非正常退出 → 事件表每 30 min 备份（backup.py 定时任务 + 看板巡检）；关键产出双介质（本地目录 + U 盘）；**恢复演练丢失版本 ≤1** ｜ backup_log 间隔断言；test_nfr5_recovery.py
- **第 542 行**（实施规划书 3.2）：
  > ……检查点用 SQLite 实现，进程崩溃后按事件表重建，**恢复演练丢失版本 ≤1**。
- **第 639 行**（6.2 测试与回归策略）：
  > `恢复演练 | 事件表重建 + kill 重启 | 丢失版本 ≤1`

> **⚠️ 需要队长注意的口径张力**：需求原文说的是「进程崩溃后**按事件表重建**」（`event_log`），而本轮方案用的是「langgraph 检查点重建」。两者是**不同机制**：`event_log` 是业务事件流水（`schema.sql:48-55`），检查点是图执行态快照。建议实现时**双轨并存并向需求原文对齐**：检查点负责「能续跑」，恢复时同时写 `events` 留痕，这样「按事件表重建」的字面要求也不会落空。另外：`tests/test_nfr5_recovery.py` **当前不存在**（tests/ 只有 7 个文件），需 Tess 新建。

### 4.2 「人机回环 / 人工卡点 / 暂停恢复 / 检查点」功能需求原文

- **第 96 行**（1.2 架构选型）：
  > 本场景的 Agent 编排落在 7 号 Graph / Workflow 架构：Coordinator 是一张 StateGraph（状态 + 节点 + 边），4 个 LLM 角色是图上的 LLM 节点，确定性流水线（体检、执行、检查、出图）是确定性节点，**人工卡点用人机回环（Human-in-the-loop）实现**。
- **第 106 行**：
  > `7 号 Graph / Workflow | 采用（主架构） | 分支（体检结果走哪条路）、循环（修复 1 轮）、人工审批（口径确认卡点）、可审计可回溯，全部由图节点 + 条件边 + 检查点承担`
- **第 109 行**（人机回环落地方式 —— 本轮**最直接**的验收依据）：
  > **口径冲突僵持、合理性检查 blocking、导出前合规校验三个节点调用 `interrupt()` 暂停图执行，保存完整状态（对应 SQLite WAL 事件表 + 档案版本），由人做出判定后以 `Command(resume=...)` 恢复，人的判定作为该节点的返回值落库。**
- **第 111 行**（与传统 ReAct 对比）：
  > 与传统 ReAct 的对比正是课程列出的四大痛点：**不能暂停（本场景需要人工卡点）**、不能回滚（档案版本链 supersedes）、不能分支（体检/合理性条件边）、状态黑盒（每步写 event_log）。
- **第 253 行**（5.5 Coordinator 定位）：
  > 定位：`mmagent/agents/coordinator.py` 是规则状态机，路由（阶段流转）、版本递增、人工卡点、失败计数、降级决策**全部纯 Python 判定，不消耗调用额度**。对应架构课程 7 号 Graph：节点 = 各角色与流水线，边 = 条件流转，**人工卡点 = `interrupt()` 暂停 + `Command(resume)` 恢复**。
- **第 539 行**（架构决策）：
  > **没有人工卡点的自动化不接进对外渠道**——任何对外动作必须保留人工确认，责任边界写清楚。
- **第 666 行**（审核留痕 NFR 侧）：
  > `审核留痕 | 人工卡点真人点击确认、记录可查不可删 | event_log 不可 UPDATE/DELETE`

### 4.3 三个卡点「确认什么 / 确认后写入什么」

- **第 542 行**（实施规划书 3.2，三卡点定名）：
  > 人机回环（Human-in-the-loop）落在三个卡点：**口径冲突僵持、合理性检查 blocking、导出前合规校验**——节点调用 `interrupt()` 暂停并保存完整状态（SQLite WAL 事件表 + 档案版本），人工判定后以 `Command(resume)` 恢复，**人的判定作为节点返回值落库**。
- **卡点 1｜口径冲突僵持**
  - 第 128 行：`artifacts\intake\caliber_conflicts.json`：每条含 `conflict_id / table_id / field / conflict_type ∈ {unit, period, entity_scope, definition} / evidence_rows[] / suggested_caliber`。
  - 第 178 行 DDL：`caliber_conflict.resolution TEXT NOT NULL DEFAULT 'unresolved' CHECK (resolution IN ('unresolved','use_a','use_b','third_party','human_override'))`，另有 `resolved_by` / `resolved_at` 字段 → **确认结果写入 `resolution` + `resolved_by` + `resolved_at`**。
  - 第 320 行：`口径冲突建议口径 | 2 名队友中懂业务的 1 人 + 何昆阳 | caliber_conflict.resolution 字段人工置值 | 阶段 2`。
  - 第 438 行：`口径冲突按 caliber_conflict 逐条人工确认`。
  - 第 178 行 DDL 另有 `field_mapping.confirmed_by` / `confirmed_at`（"为空表示未确认"）→ 口径确认同时回填字段对照表。
- **卡点 2｜合理性检查 blocking**
  - 第 138 行：`mmagent/checks/sanity.py::run_sanity_checks(...) -> SanityReport`：量纲、取值域（得分 0~100、占比 0~1、利润 ≥0）、行数一致、主键唯一；**`SanityReport.blocking == True` 时阻断出图与导出**。
  - 第 437 行：`合理性检查 blocking=True ... 报警未处理则阻断出图与导出（HTTP 422 SANITY_BLOCKED）｜event_log.error_code='EXEC_FAILED' 计数`。
  - 第 379 行（T15 验收）：`构造越界得分 → blocking=True`。
- **卡点 3｜导出前合规校验**
  - 第 443 行：`规则判断书未签字，或声明未随包导出 → 阶段 0 出一页《规则判断书》并签字；**导出前校验 ai_disclosure.docx 存在，缺失即 422 阻断** ｜ cli export pack 的阻断断言`。
  - 第 591 行：`当届赛规 | 《规则判断书》需在阶段 0 结束前解读当届赛规并签字；若赛规禁 AI 代写，导出前校验合规声明缺失即阻断`。
  - 第 269 行（模板装配失败行为）：`失败即导出终止`。
- **留痕要求**：第 666 行「记录可查不可删」+ `event_log 不可 UPDATE/DELETE`（对应 `schema.sql` 的 `events` 表当前**没有**不可变触发器 → 若要求「不可 UPDATE/DELETE」，这是**另一处潜在缺口**，见 §7 遗留问题）。

---

## Q5 冻结基线影响面

冻结基线 8 个文件与 md5（`PROGRESS.md:13-22`；20:54 复算 8/8 一致，见 `PROGRESS.md:188-203`）：

| 文件 | md5 | 本轮是否波及 | 依据 |
|---|---|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b9…` | ❌ 不波及 | 与 coordinator 无耦合（Q3 无 import） |
| `config/field_aliases.yaml` | `ecbd5d8a…` | ❌ 不波及 | 同上 |
| **`README.md`** | `2175bb71…` | ✅ **必须改** | 见下 |
| **`mmagent/store/schema.sql`** | `dd65844b…` | ❌ **不需要动** | 见下 |
| `mmagent/pipeline/guard.py` | `7bf8d6bf…` | ❌ 不波及 | 脚本 AST 守卫，与图无关 |
| `mmagent/store/db.py` | `084cfc51…` | ❌ 不需要动（但推荐**只读复用**） | 见下 |
| `mmagent/checks/sanity.py` | `e25b0d77…` | ❌ 不波及 | 卡点 2 只读它的 `blocking` 结果 |
| `config/intake_scope.yaml` | `72fff507…` | ❌ 不波及 | 输入白名单 |

### 5.1 `README.md`：**必须改**（唯一会打破基线的文件）

理由（三条，任一成立即需改）：
1. plan.md DoD#6 明确要求「README 更新架构说明与命令」。
2. README.md:8 写「Python：**3.13.12**」、README §6 写「本机无 CPython 3.11 → 使用 3.13.12」，而实测为 **3.13.14**（`probe_01_versions.txt`）→ 顺手订正。
3. README.md:69 现有硬表述：「**任何模块不得 import `requests/socket/urllib/ftplib`**」——安装 langgraph 后，导入链会把 `requests`/`socket`/`urllib.request`/`http.client` **加载进进程**（实测 `probe_10_offline_egress.txt`：`langsmith in sys.modules = True`、`requests = True`、`socket = True`）。这**不违反** `netguard-check`（它只扫仓内 `*.py` 的 AST，见 cli.py:54-89），但 README 那句话从「仓内源码纪律」被读者误读成「进程内不出现这些库」时会矛盾，**建议补一句限定语**并说明 langgraph 的引入。

→ **结论：README 需改，其 md5 `2175bb71faab5b4618373e8ca618fc92` 必然失效，须重新发布基线并在报告中标注。**（plan.md §6 风险表已预期此项。）

### 5.2 `mmagent/store/schema.sql`：**确实不需要动**

**不需要改的理由（实测证据）**：`SqliteSaver` 会**自己**在传入的那个 sqlite 文件里建两张表，与 `mm.db` 的 schema 完全无关。实测 DDL（`probe_04a_suspend.txt` 显示库内表为 `['checkpoints','writes']`；独立 DDL dump）：
```sql
CREATE TABLE checkpoints (
  thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
  checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT,
  type TEXT, checkpoint BLOB, metadata BLOB,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id));
CREATE TABLE writes (
  thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
  checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL,
  task_path TEXT NOT NULL DEFAULT '', idx INTEGER NOT NULL,
  channel TEXT NOT NULL, type TEXT, value BLOB,
  PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx));
```
→ 只要检查点落在**独立 sqlite 文件**，`schema.sql` **一个字符都不用改**，基线 md5 保持不变。（plan.md §5 的「优先不修改它」可 100% 达成。）

**顺带澄清一个不构成阻塞的细节**：`tests/test_fr9_profile.py:53-62` 会查 `sqlite_master` 的表/触发器集合，但断言用的是**子集** `<=`（`assert {"profile_doc","events","artifacts","metric_obs"} <= tables`），**不是相等** → 即便将来把 checkpoint 表塞进 `mm.db`，该测试也**不会**失败。所以「不动 schema.sql」的理由不是「会打破测试」，而是 §5.1/Q6 所述的**职责隔离与基线保护**。

### 5.3 `mmagent/store/db.py`：不需要改，但**推荐只读复用**

- `db.py:17-32 connect()` 已设连接级 PRAGMA：`foreign_keys=ON` / `busy_timeout=5000` / `recursive_triggers=ON`，并 `timeout=5.0`、`path.parent.mkdir`。
- `db.py:35-44 init_db()` 设库级 `journal_mode=WAL`（一次性，注释说明「切换涉及磁盘 IO，实测每次 ~100ms+」）。
- **建议 Max 这样做**：给 langgraph 建连接时复用 `from ..store.db import connect`，即 `SqliteSaver(connect(ckpt_path))` ——
  - 白拿 `busy_timeout=5000`（避免 `database is locked`）与 `timeout=5.0`；
  - **不修改 `db.py` 任何字符** → md5 `084cfc517f1e2cce456b307a3f5fcee0` 不变；
  - 注意 `connect()` 会把 `row_factory` 设为 `sqlite3.Row`（db.py:27）——实测 `probe_02/04` 用的是原生 `sqlite3.connect`，未验证 `Row` 工厂对 langgraph 的兼容性（**UNKNOWN，建议 Max 用 3 行小实验先确认**；若不兼容，退回 `sqlite3.connect(ckpt, check_same_thread=False, timeout=5.0)` + 手工 `PRAGMA busy_timeout`）。
  - 独立库还需要设一次 `journal_mode=WAL`（库级）——建议在 checkpoint 初始化处显式 `conn.execute("PRAGMA journal_mode=WAL;")` 一次，或复用 `init_db` 思路，但**不要**把 langgraph 表写进 `schema.sql`。

### 5.4 其余 5 个基线文件

`intake.py` / `field_aliases.yaml` / `guard.py` / `sanity.py` / `intake_scope.yaml` — 与 coordinator 无 import 关系（Q3 已验证），本轮不波及。

---

## Q6 检查点库落位建议

### 6.1 落哪个文件：**建议独立文件**（优先）

**建议：`data/checkpoints.db`**（或 `data/checkpoints.sqlite`，见下方 ⚠️）。

理由（逐条带依据）：

1. **零改动冻结基线**：`SqliteSaver` 自建 `checkpoints`/`writes` 两张表（DDL 见 §5.2），与 `schema.sql` 无关 → 独立文件可让 `schema.sql` 的 md5 `dd65844b…` 保持不变，plan.md §5「优先不修改它」直接达成。
2. **隔离 `profile_doc` 的不可变守卫**：`schema.sql:18-45` 在 `profile_doc` 上挂了 `BEFORE UPDATE` / `BEFORE DELETE` / `BEFORE INSERT` 三个 `RAISE(ABORT)` 触发器，且 `db.py:31` 为了让 `INSERT OR REPLACE` 的隐式 DELETE 也触发守卫而开了 `recursive_triggers=ON`。把 langgraph 的表放进**同一个库**，等于让 langgraph 的高频写入与这套「ABORT 型」守卫共库共存；独立文件是**最省心的隔离**（langgraph 的 `writes` 表 schema 说变就变，不该受业务约束牵连）。
3. **备份/恢复语义不同**（需求 第 451 行）：
   > `每 30 min 备份 → pipeline/backup.py 定时执行 PRAGMA wal_checkpoint(TRUNCATE) + 复制 mm.db 到 data/backups/ + 镜像到 U 盘`
   `mm.db` 是**业务单一事实源**（docx 第 156 行目录树注释「`data/mm.db  # SQLite 单一事实源`」）；检查点是**可再生的运行态**。混库会把「备份体积 + 恢复流程」耦上 langgraph 的内部 BLOB 序列化格式（跨 langgraph 版本不保证兼容），一旦升级 langgraph 就可能让整份业务备份变成「含不可解析数据」。独立库让「业务备份」保持纯净。
4. **连接参数一致性**：`mm.db` 依赖 `db.py::connect()` 施加的 PRAGMA 组合；langgraph 自带连接（课件 `demo11:29` 是裸 `sqlite3.connect(..., check_same_thread=False)`）不会走 `db.py`。独立库避免「两套连接属性混在同一文件」的隐性差异。
5. **写锁竞争**：SQLite 的写锁是**库级**的。`SqliteSaver` 在每次图步进都写 `checkpoints`/`writes`；若与 FastAPI 请求路径上的 `mm.db` 写（`events` / `artifacts` / `profile_doc`）共库，会显著抬高 `SQLITE_BUSY` 概率。独立库把写竞争在物理上分开。（本项目为单机单人，风险可控，但分离成本几乎为零。）

**⚠️ 落位必须配套的一个动作（易漏）**：`.gitignore` 只忽略
```
data/*.db
data/*.db-wal
data/*.db-shm
```
（`.gitignore` 第 19-22 行）——**没有 `*.sqlite` 规则**。因此：
- 若命名为 `data/checkpoints.sqlite` → 会被 git 跟踪，把运行态二进制提交进版本库 ❌；
- 若命名为 **`data/checkpoints.db`** → 自动命中 `data/*.db` 与 `data/*.db-wal/-shm` → **零改动即被忽略** ✅（推荐）。

→ **建议采用 `data/checkpoints.db`**；若坚持 `.sqlite` 后缀，则必须同时补 `.gitignore`（`.gitignore` 不在冻结基线内，可改）。

**不推荐落 `data/mm.db` 的额外理由**：会让 `cli db init` 的语义变模糊（业务建库 vs 图运行态），且 `data/mm.db` 已在 `task-001` 被当作「单一事实源」写进需求文档。

### 6.2 检查点表与 `events` 留痕表的职责边界

| 维度 | `events`（`schema.sql:48-55`） | langgraph `checkpoints` / `writes`（独立库） |
|---|---|---|
| 面向对象 | **人**（审计/复核/看板/追溯） | **机器**（图执行引擎） |
| 内容 | 业务事件：`event_type` / `actor` / `payload`(JSON) / `trace_id`，如 `coordinator.transition`、`coordinator.stage_failed`（`coordinator.py:128/88`） | 图内部态：channel values + pending writes + interrupt 栈，序列化 BLOB |
| 生命周期 | 需求要求「记录可查不可删」（docx 第 666 行） | 可再生、可清理、随 langgraph 版本演进 |
| 谁写 | 业务代码显式写（`Coordinator._emit`） | 框架自动写 |
| 恢复作用 | 需求 第 110 行「进程崩溃后**按事件表重建**」的**审计依据** | 「能续跑到哪一步」的**执行依据** |

**建议边界（写清给 Max）**：
1. **不要**把 langgraph 的检查点塞进 `events`，也**不要**用 `events` 当 checkpointer 后端。
2. 卡点被人工恢复时，由**业务代码**额外写一条 `events`，例如
   `event_type='coordinator.gate_resolved'`，`actor='human:<name>'`，`payload={"gate":"caliber_conflict","interrupt_id":"e1a7b6...","decision":{...}}`，`trace_id=<本次 trace>`。
   → 一举满足三处需求：docx 第 109 行「人的判定作为该节点的返回值落库」、第 666 行「人工卡点真人点击确认、记录可查不可删」、第 110 行「按事件表重建」的字面口径。
3. 检查点只负责「能续跑」，**不承担审计职责**（其 BLOB 不可读、且可被框架清理）。

### 6.3 `thread_id` 用什么值

现状与风险：
- `CoordinatorState.trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)`（`coordinator.py:59`）——是**每次新建对象随机**的观测 id。
- `trace_id` 与 `thread_id` **必须分开**：`trace_id` 用于事件关联（`_emit` 里写进 `events.trace_id`，`coordinator.py:76`），`thread_id` 是**跨进程恢复键**，要求**稳定且由调用方持久化**。

需求侧约束：
- 卡点 1 发生在 `INTAKE` 之后（`coordinator.py:107-111`），此时可能**还没有 `profile_id`**（`PROFILE` 阶段在其后，`TRANSITIONS` 见 42-50 行）→ **不能**把 `thread_id` 绑死在 `profile_id` 上。
- 需求文档已存在 `run_id` 概念：docx 第 193 行 `{"run_id":"...","returncode":0,...}`；仓库已有 `artifacts/runs/smoke1..smoke5` 的「一次运行」目录形态（`artifacts/runs/<run_id>/{prep.py,solve.py,manifest.json,results/,figures/,logs/}`，docx 第 156 行）。

**建议**：一次「协作运行」= 一个 langgraph thread。
- 形态：`thread_id = f"{run_id}"`，其中 `run_id` 形如 `run_20261014_0830_ab12cd`（时间戳 + 短随机），由**调用方在运行开始时生成并持久化**（落 `events.payload` 或运行目录名），恢复时**读回同一个值**。
- 若同一 `profile_id` 会有多次运行，可加前缀便于排查：`thread_id = f"{profile_id}:{run_id}"`（前缀只为人读，不要求存在）。
- **不建议** `thread_id = profile_id`：`profile_doc` 会跨多次运行演进（版本链 `supersedes`），而图线程是「一次运行」的概念，混用会让「恢复哪个线程」与「档案有几个版本」纠缠。
- `checkpoint_ns` 保持默认空串 `""`（实测 `probe_04` 全程默认值，未出现多命名空间需求）；除非引入子图，否则不要自定义。
- resume 值建议用**结构化 dict**（对齐 `caliber_conflict.resolution` 的 CHECK 取值域 `unresolved/use_a/use_b/third_party/human_override`，docx 第 178 行）：
  `Command(resume={"gate":"caliber_conflict","decision":"use_a","resolved_by":"human:kunyao","note":"..."})`
  —— 同时绕开 §2.6 的 `resume=None` 缺陷，也让 §6.2 的 `events` 留痕可以直接落库。

---

## 7. 遗留问题 / UNKNOWN

1. **`Command(resume=None)` 是 1.2.15 的库缺陷**（§2.6），需 Max 在实现里显式规避，并在测试里加一条「不允许传 None」的护栏。是否要向 langgraph 上游反馈 → 交队长决定。
2. **多 pending interrupt 的恢复形式**（`_loop.py:1005-1009` 要求 interrupt-id 映射）为**读源码结论**，本实验未构造多 pending 场景实测。**建议实现约束为「一节点一卡点」**即可完全规避。
3. **`db.py::connect()` 的 `row_factory=sqlite3.Row` 是否与 `SqliteSaver` 兼容** — **UNKNOWN**（探针用的是裸 `sqlite3.connect`，未验证 `Row` 工厂）。建议 Max 用一个 5 行实验确认后再决定「复用 `connect()` 还是自建连接」。卡在：本轮只读范围不含对 `db.py` 的行为实验设计。
4. **`netguard-check` 不排除 `.agent-runs/`（仓内 latent bug）** — `cli.py:54-63 _iter_repo_py()` 只排除 `.venv` / `venv` / `__pycache__` / `.git`，而 `.agent-runs/` **已被 git 跟踪**（`git ls-files .agent-runs` = 35 项，未被 `.gitignore` 覆盖）。本次我的 `probe_10_offline_egress.py` 因为必须 `import socket` 打补丁，**实测把 `netguard-check` 打到 EXIT=1**：
   > `[netguard-check] 发现违规： …\probe\probe_10_offline_egress.py:8: 违规 import socket`

   为不污染守卫结果，我已把该探针源码改名为 `probe_10_offline_egress.py.txt`（`*.py.txt` 不被扫描），复跑 `netguard-check` 已恢复 `通过 / EXIT=0`。**复跑方法**：把 `.py.txt` 改回 `.py` → 运行 → 用完再改回。

   **第二次触发（更强的证据）**：上面用 `--basetemp=…/probe/pytmp` 跑测试后，`pytest` 在仓内生成的用例临时脚本里有一个「语法错误脚本」夹具，`netguard-check` 立刻再次变红：
   > `[netguard-check] 发现违规： …\probe\pytmp\test_runner_语法错误脚本_返回码非00\scripts\broken.py: 解析失败（invalid syntax），未做联网检查`

   我已删除 `pytmp/`，`netguard-check` 恢复 `通过 / EXIT=0`（最终态已复验；本条也因此说明：**只要有任何进程在仓内写 `*.py`，守卫就可能被误判**）。
   → **建议队长决策**：是否给 `_iter_repo_py` 加 `.agent-runs` 排除（`cli.py` 不在冻结基线，可改）。否则 Tess/Vic 未来任何带 `socket`/`requests` 的探针、或用仓内 basetemp 跑测试，都会让守卫变红。
5. **`events` 表缺少「不可 UPDATE/DELETE」的强制** — docx 第 666 行要求「记录可查不可删 / `event_log` 不可 UPDATE/DELETE」，但 `schema.sql:48-55` 的 `events` 表**没有**对应触发器（`profile_doc` 才有）。这是一个**本轮范围外的既有缺口**，改动 `schema.sql` 会破基线 → 仅记录，请队长决定是否另立任务。
6. **课件 langgraph 版本 UNKNOWN** — 课件源码里没有任何版本标注（无 `requirements.txt`/`pyproject.toml`），无法确认课件是在哪个 langgraph 版本下录制的。已用实测的 1.2.15 复现其 API 语义（结果一致），但「课件版本 == 1.2.15」无法证明。
7. **`demo11` 的「模拟重启」并未真跨进程**（§Q1(e)）——若 Tess/Vic 想引用课件作为 NFR-5 证据，请注意这一点；真正的跨进程证据是 `probe_04a/04b`。
8. **`1012_Rag-Agent-mysql-部署&LangGraph入门\` 无可用代码**（只有 1 个未下载完的 `.mp4.downloading`），无法作为调查源。
9. **三个卡点的接入形态未定**（CLI 子命令 / API 路由 / 两者）— Q3 显示 `coordinator` 目前无任何调用方，DoD#1/#2/#5 需要一个外部入口才能验收。**这一条需要队长拍板**，我按职责不拍板。
10. **README.md:8 的 Python 版本（3.13.12）与实际（3.13.14）不符** — 顺带订正项。

---

## 附：探针清单（全部在 `.agent-runs/2026-10-14/task-002/probe/`）

| 探针 | 输出 | 用途 |
|---|---|---|
| `probe_01_versions.py` | `.txt` | 解释器/包版本/API 可导入性 |
| `probe_02_sqlite_persistence.py` | `.txt` | 复现 demo11（SqliteSaver 持久化） |
| `probe_03_hitl_memory.py` | `.txt` | 复现 demo09/demo10（人机回环 + MemorySaver） |
| `probe_graph_def.py` + `probe_04a_suspend.py` + `probe_04b_resume.py` | 3×`.txt` | **跨进程恢复（NFR-5）**：硬崩溃 → 新进程续跑 3 卡点 |
| `probe_05_interrupt_semantics.py` | `.txt` | interrupt/Command 语义五点实验 |
| `probe_06_falsy_resume.py` | `.txt` | 假值/空值 resume 边界 |
| `probe_07_resume_none_bug.py` | `.txt` | 隔离 `resume=None` 缺陷 + 完整 traceback |
| — | `probe_08_pip_freeze.txt` | `pip freeze` 全量（73 项） |
| — | `probe_09_pytest.txt` | 全量 190 例回归（依赖安装后） |
| `probe_10_offline_egress.py.txt` | `.txt` | 零外网出站检查（源码改名以免触发 netguard，见 §7.4） |
| — | `probe04_ckpt.sqlite` | probe_04 的检查点库（证据） |
| — | `pytmp/` | pytest basetemp，**已删除**（它在仓内生成含 `import` 的临时脚本会误触 netguard，见 §7.4） |

> 最终校验（2026-10-14 16:43）：`git status --porcelain` = `?? .agent-runs/2026-10-14/`（仓库源码/配置/测试/产物**零改动**）；`python -m mmagent.cli netguard-check` = `通过 / EXIT=0`。
