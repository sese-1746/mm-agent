# mmagent —— 数学建模竞赛协作 Agent（全本地、零云支出）

面向 72 小时数模竞赛的本地协作 Agent：附件体检 → 口径确认 → 建模规划 → 求解执行 →
结果理智性检查 → 论文级出图 → 合规披露，全流程本地推理（Ollama），无任何云端 API 调用。

## 1. 环境

- Python：**3.13.14**（本机无 CPython 3.11，故使用 3.13，已记入 KNOWN_ISSUES）
- 虚拟环境：`H:\mm-agent\.venv\Scripts\python.exe`
- 安装依赖：

```bat
H:\mm-agent\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Ollama 前置

- 服务地址：`http://localhost:11434`（只允许本机回环，禁止外网）
- 模型（写入 `config/models.yaml`，代码中禁止硬编码模型名）：
  - 生成模型：`qwen3.5:latest`
  - 降级模型：`qwen3.5:0.8b`
  - 向量模型：`qwen3-embedding`
- 确认在线与模型列表：

```bat
H:\mm-agent\.venv\Scripts\python.exe -m mmagent.cli env-attest --out artifacts\reports\env_attest.json
```

## 3. 快速开始

```bat
cd /d H:\mm-agent

:: 1) 初始化数据库（WAL + 触发器）
.venv\Scripts\python.exe -m mmagent.cli db init --db data\mm.db

:: 2) 附件体检（数据目录只读参考：C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题）
.venv\Scripts\python.exe -m mmagent.cli intake --dataset "C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题" --out artifacts\intake

:: 3) 静态合规检查（仓库内 *.py 不得直连外网）
.venv\Scripts\python.exe -m mmagent.cli netguard-check

:: 4) 启动服务
.venv\Scripts\python.exe -m uvicorn mmagent.app:app --host 127.0.0.1 --port 8000
```

启动后可访问 `http://127.0.0.1:8000/docs` 查看接口。

## 4. HTTP 接口（/api/v1）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/intake/run` | 附件体检（`{"dataset_dir":..,"out_dir":..}`） |
| POST | `/api/v1/solve/run` | 守卫 + 执行脚本 |
| GET | `/api/v1/profile/{profile_id}` | 读取档案版本（`?version=last`） |
| POST | `/api/v1/compliance/disclosure` | 生成 AI 使用披露（md + docx） |

统一错误体：`{"error_code": ..., "message": ..., "detail": ..., "trace_id": ...}`。

## 5. 架构与依赖方向

```
api → agents → tools → pipeline → store → db
```

- `store/`、`checks/` 不得 import `agents/` 或 `api/`；
- `tools/` 为纯确定性薄封装，**不得** import `agents/`；
- 全本地：**仓库内源码**不得 import `requests/socket/urllib/ftplib`（由 `netguard-check` 静态校验），Ollama 仅走 `localhost:11434`。
  - 说明：引入 `langgraph` 后，进程内会间接加载 `requests` / `socket` / `urllib` 等（上游依赖链），这不违反本纪律——纪律约束的是**仓内 `*.py` 源码**，不是运行时进程的 `sys.modules`。
  - `netguard-check` 已排除 `.venv/`、`.agent-runs/`（调查/测试/验证探针）等非产品目录。
- `mmagent/rag/adapter.py` 是全仓库唯一允许引用 `RAG_ROOT` 的模块（当前占位未启用）。

## 6. 已知偏差（KNOWN_ISSUES）

- 本机无 `qwen3:4b` → 降级链改为 `qwen3.5:latest` → `qwen3.5:0.8b`；
- 本机无 CPython 3.11 → 使用 3.13.14（托管运行时）；
- `repair.py`、`intake_llm.py`、`rag/adapter.py`、各角色 `agents/*.py` 为骨架，
  待后续阶段接入 LLM 与 RAG。

## 7. 人机回环（LangGraph interrupt + SqliteSaver）

`mmagent/agents/coordinator.py` 是 LangGraph `StateGraph` 实现：三个人工卡点用
`interrupt()` 暂停、用 `Command(resume=...)` 恢复，检查点落**独立** SQLite 库
（默认 `data/checkpoints.db`，含 `checkpoints` / `writes` 两表，与业务库 `data/mm.db` 隔离）。

### 7.1 三卡点与裁定契约

| 卡点 | gate 名 | 触发条件 | resume 值（必需键 + 取值域） |
|---|---|---|---|
| 口径冲突 | `caliber_conflict` | `caliber_conflicts=True` 且未确认 | `{"resolution": "use_mapping"\|"keep_raw"\|"human_override", "note": str}` |
| 合理性阻断 | `sanity_blocking` | `sanity_blocking=True` | `{"decision": "accept"\|"fix_and_rerun"\|"abort", "note": str}` |
| 导出前合规 | `pre_export_compliance` | 未确认合规 | `{"decision": "approve"\|"reject", "note": str}` |

`interrupt()` 的 payload 必带 `gate` / `stage` / `question` / **`review`** 四键：前三键供调用方
判断该投什么裁定，`review` 携带**待审材料**（口径冲突明细 / 阻断项明细 / 缺失披露项；无材料时为空容器，绝不为 `None`）。

### 7.2 状态图（裁定驱动控制流 + halt + 回边）

```
START → intake → gate_caliber ─(→profile)→ profile → plan → solve → gate_sanity
      ├─ accept ────────────────────────────────────────────────→ render → assemble → gate_compliance
      ├─ fix_and_rerun ─(rerun_solve，最多 MAX_RERUN=1 次)→ solve
      └─ abort ─────────────────────────────────────────────────→ halt → END
gate_compliance ├─ approve → export → END
                └─ reject  → halt  → END
```

三条卡点出边是 **`add_conditional_edges`**（裁定**驱动**去向，不再"只记录不消费"）；
回边 `fix_and_rerun` 有迭代上限 `MAX_RERUN`，超限转 `halt`（防无限循环）。
`thread_id` = 调用方传入的 `run_id`（跨进程恢复键）。

### 7.3 命令行

> **前置步骤**：`run start` / `run resume` 会把留痕写进台账库，**必须先初始化台账库**
> （生成 `events` 表）。未初始化时命令会**明确报错并 exit 3**（不会静默丢事件，也不会自动建库）。

```bat
:: 0) 前置：初始化台账库（事件留痕表）
.venv\Scripts\python.exe -m mmagent.cli db init --db data\mm.db

:: 启动一次运行（--conflicts 触发卡点1，--sanity-blocking 触发卡点2）
.venv\Scripts\python.exe -m mmagent.cli run start --run-id demo-1 --conflicts --sanity-blocking

:: 查看当前阶段与是否挂起
.venv\Scripts\python.exe -m mmagent.cli run status --run-id demo-1

:: 投递人工裁定，从挂起点恢复（--decision 为 JSON）
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-1 --gate caliber_conflict --decision "{\"resolution\":\"use_mapping\",\"note\":\"按建议\"}"
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-1 --gate sanity_blocking --decision "{\"decision\":\"accept\",\"note\":\"已复核\"}"
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-1 --gate pre_export_compliance --decision "{\"decision\":\"approve\",\"note\":\"合规确认\"}"
```

- **挂起是正常状态** → `run start` 命中卡点时 **exit 0**，并打印结构化 JSON
  （`run_id/stage/paused/gate/question/done/halted/review/log`）；
- **`HALTED` 也是正常终态** → 人工 `reject`/`abort` 或回边超限时 `stage="HALTED"`、`halted=true`、`done=false`，
  命令仍 **exit 0**；
- 投错 gate / 非法 decision（`null`、`{}`、越界值、非 JSON）→ **exit 2** + 明确错误信息；
- **台账库未初始化（缺 `events` 表）→ exit 3** + 提示先跑 `db init`（不自动建库）；
- **`run status` 的 run_id 不存在 → exit 4**（与 2/3 区分）；台账库未初始化同样 exit 3；
- 检查点库可用 `--ckpt` 覆盖（默认 `data/checkpoints.db`）；
- 若台账库在运行中途变得不可写，留痕会**降级**：首次失败往 stderr 打一条警告（含真实异常 + 修复提示），不再重复刷屏，但流程不中断。

> 待审材料可用 `--conflict-detail '<JSON>'` / `--sanity-detail '<JSON>'` 注入（缺省用内置示例）。

### 7.4 ⚠️ 两个必须记住的 pitfall（langgraph 1.2.15 实测）

1. **永不传 `resume=None`**：langgraph 1.2.15 在 `langgraph/pregel/_loop.py` 会抛
   `UnboundLocalError`（库自身缺陷）。也**永不传空 dict `{}`**（`Command(resume={})` 会静默不恢复）。
   本项目的 `Coordinator.resume()` 会在调用 langgraph **之前**校验，非法值抛
   `InvalidGateDecision`，绝不把非法值传进去。
2. **`resume` 会从该节点开头重跑**：`interrupt()` **之前**的代码每次恢复都会再执行。
   因此卡点节点内**不得**有非幂等写操作；`events` 留痕一律由 driver（`start()`/`resume()`）写，
   一次性逻辑放在 `interrupt()` 之后。一个节点只放一个卡点。

### 7.5 裁定 → 去向语义表（冻结）

| 卡点 | 裁定值 | 去向 | 终态 |
|---|---|---|---|
| `caliber_conflict` | `use_mapping` / `keep_raw` / `human_override` | → `profile`（继续） | — |
| `sanity_blocking` | `accept` | → `render`（继续） | — |
| `sanity_blocking` | `fix_and_rerun` | → 回 `solve` 重跑（≤ `MAX_RERUN`=1 次），再**重新评审** | 超限 → `HALTED` |
| `sanity_blocking` | `abort` | → `halt` | `HALTED`（`done=False`） |
| `pre_export_compliance` | `approve` | → `export` | `DONE`（`done=True`） |
| `pre_export_compliance` | `reject` | → `halt`（**绝不进 export**） | `HALTED`（`done=False`） |

`HALTED` 是**正常终态**（非异常）：`stage="HALTED"`、`halted=true`、`paused=false`、`done=false`；
driver 写 `hitl.run_halted` 事件（payload 含 gate + 裁定原文）。

### 7.6 payload 的 `review` 键（待审材料）

`interrupt()` payload 恒含 `review` 键，携带该卡点的待审材料：口径冲突明细 / 阻断项明细 / 缺失披露项。
**无材料时为空容器（`{}`/`[]`），绝不为 `None`**（避免调用方 `payload["review"]` 直接 `None` 崩溃）。
`run start` / `run status` 的 stdout JSON 里 `review` 可见。

### 7.7 ⚠️ 澄清课件错误表述：`interrupt()` **不**保存"完整状态"

HITL 课件 P2 称「`interrupt()` 保存完整状态（包括内存、文件、网络连接）」——**实测不成立**：
它只把 **channel 状态**序列化成检查点 BLOB（默认落在 `SqliteSaver`），**不**保存进程内存、文件句柄或网络连接。
恢复时是**从卡点节点开头重跑**：`interrupt()` 之前的代码会**再执行一遍**（副作用会重复）。
因此本项目的纪律是：卡点节点内不得有非幂等写操作；`events` 留痕一律由 driver 写；一次性逻辑放在 `interrupt()` 之后。

### 7.8 有意偏离课件：`SqliteSaver(sqlite3.connect(...))` 而非 `from_conn_string`

HITL 课件 P24 推荐 `SqliteSaver.from_conn_string("xxx.db")`。本项目**刻意不用**，改为
`SqliteSaver(sqlite3.connect(path, check_same_thread=False, timeout=5.0))` + `PRAGMA busy_timeout=5000`：

- `from_conn_string` 是 **contextmanager**，`with` 退出即关闭连接 —— **不适合长驻实例 + 跨进程恢复**；
- 它**不设** `busy_timeout`，并发下更易 `SQLITE_BUSY`；
- 本项目也**未**复用 `store.db.connect()`：后者未设 `check_same_thread=False`，而 langgraph 在 worker 线程
  写检查点，会抛 `ProgrammingError`（见 `.agent-runs/2026-10-14/task-002/probe/probe_11_max_conn_compat.py`）。

> 请勿按课件改回 `from_conn_string`。

### 7.9 ⚠️ 待用户确认的偏差：口径冲突裁定**取值域**

需求 docx 用 `use_a` / `use_b` / `third_party`，本实现用 `use_mapping` / `keep_raw` / `human_override`
（可读性更好，且 docx 的 A/B 第三方数据框架本项目未实现）。**本轮保留实现值**，映射如下，待用户确认：

| docx 取值 | 本实现取值 | 语义 |
|---|---|---|
| `use_a` | `use_mapping` | 采用建议的 `field_mapping`（合并到统一口径） |
| `use_b` | `keep_raw` | 保留原始口径（不合并） |
| `third_party` | `human_override` | 人工另行指定口径 |

## 8. 静态守卫（FR-5）— import 白名单

LLM 生成的脚本执行前必过 `pipeline/guard.py` 的 AST 检查，采用 **import 白名单**
（而非黑名单，避免 `from os import system` / `importlib.import_module` 等绕过）：

- 白名单顶层模块：`pandas / numpy / matplotlib / math / json / csv / sqlite3 /
  pathlib / statistics / itertools / collections / datetime / re / random`；
- 危险调用黑名单（补网）：`eval / exec / __import__ / compile`、
  `os.system / os.popen / subprocess.* / importlib.import_module`；
- 禁止相对导入。违规抛 `ScriptGuardError`（含全部违规行号）。

## 9. 数据解析纪律与已知数据问题

1. **跨表汇总必须按门店 id `join`，禁止按行号对齐**：附件1 的门店顺序与其他附件不同
   （乱序），附件2~6 为 `A1..A5,B1..B7` 顺序。
2. 附件4/5 表标题明写「空格处为0」：空缺即 0（`fillna_rule`，非缺失）。
3. 附件6 的「利润定额」存在逐行全等的月份组（`def_1_2`/`def_3_11`/`def_7_8`），
   「评级」另有一组（`qual_1_2_7_8_10`）——建模时勿去重，注意共线。
4. 附件3 有 17 处文本混入数值列（`'97.3.4'`，见 `dirty_cells.json`），
   只留痕不改值，需业务确认。
5. 附件1 的 `N` 列为派生列（各月得分算术均值），见 `unmapped_columns.json`。
6. 输入白名单见 `config/intake_scope.yaml`：只吃 `总表.xlsx` + `附件1~6`，
   `预处理*.xlsx` 记入 `skipped_files`。
7. `table_id` 采用文档口径：文档仅**显式定义** `att1_satisfaction` / `att5_complaint` /
   `att6_profit` 三个；`att2_service` / `att3_clean` / `att4_complaint` 为**按同构规则外推**。
   旧/外推名（`att6_profit_quota`、`att5_problem`）见 `config/field_aliases.yaml` 的 `legacy_table_ids` 兼容映射。
