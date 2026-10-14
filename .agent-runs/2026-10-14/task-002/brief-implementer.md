# brief-implementer.md — 实现工程师 Max（task-002）

- **role_name**: 实现工程师 Max
- **mission**: 把 `mmagent/agents/coordinator.py` 从纯 Python 状态机改造为 **LangGraph `StateGraph` + `interrupt()` + `SqliteSaver`** 实现，让三个人工卡点可暂停、可跨进程恢复；并补上 CLI 入口、依赖锁定与 README。
- **allowed_edit_scope**（**严格**）:
  - 可写：`mmagent/agents/coordinator.py`、`mmagent/agents/__init__.py`、`mmagent/cli.py`、`requirements.txt`、`README.md`
  - 可写：`H:\mm-agent\.agent-runs\2026-10-14\task-002\` 下的报告与产物
  - **禁止修改**：`mmagent/store/schema.sql`、`mmagent/store/db.py`、`mmagent/store/profile.py`、`mmagent/pipeline/*`、`mmagent/checks/*`、`mmagent/security/*`、`mmagent/app.py`、`mmagent/api/*`、`mmagent/tools/*`、`tests/*`、`config/*.yaml`、`.gitignore`
  - 若你判断必须动范围外文件，**先停下、回报队长**，不要擅自改。
- **output_path**: `H:\mm-agent\.agent-runs\2026-10-14\task-002\implementer-report.md`
- **success_criteria**: §验收自检 全部通过，且 task-001 的 190 例测试保持「189 passed / 1 xfailed / 0 failed」。

---

## 0. 环境与起点

- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`。**实测版本是 Python 3.13.14**（不是 3.13.12 —— README/PROGRESS 里的旧说法要更正）。
- 工作目录 `H:\mm-agent`（已 git 化，初始提交 `a901f81`，tag `mm-freeze-2026-10-13`）。
- 开工前先跑一次基线确认起点绿：`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q` → 期望 `189 passed, 1 xfailed`。
- **langgraph 已由调查员装进 `.venv`**：`langgraph==1.2.15`、`langgraph-checkpoint-sqlite==3.1.2`（均已实测可导入）。**你不需要重新安装**，只需把版本写进 `requirements.txt`（按实际 `pip freeze` 取，不要凭记忆）。
- 调查证据在 `.agent-runs/2026-10-14/task-002/investigation-report.md` 与 `probe/`（含 `probe_04a/04b` 跨进程恢复探针、`probe_05` interrupt 语义、`probe_07` 的 `resume=None` 崩溃复现）。

---

## 1. 现状（已核实）

`mmagent/agents/coordinator.py`（131 行）：
- `Stage` 枚举 9 值：`INTAKE/PROFILE/PLAN/SOLVE/RENDER/ASSEMBLE/EXPORT/DONE/HALTED`
- `TRANSITIONS` 字典给出阶段顺序
- `HumanGateRequired(gate, detail)` 异常
- `CoordinatorState(stage, fail_counts, context, trace_id)`
- `Coordinator.step()` 在三处抛 `HumanGateRequired` 并置 `HALTED` → **暂停后无法恢复**（状态只在内存）
- `MAX_CONSECUTIVE_FAILS = 2`

**关键事实**：全仓库（排除 `.venv/.git/.agent-runs`）**没有任何地方 import `coordinator`**，只有 `config/models.yaml:32` 有一个 `coordinator:` 预算键。所以改造**零破坏**，但**你必须自己加入口**，否则无法验收。

---

## 2. 调查结论中你必须遵守的硬约束（都有实测证据）

1. **恢复必须用 `Command(resume=X)`**。plain `invoke(None, config)` 实测**不会恢复**，只会重进节点再次挂起（不报错也不推进）。
2. **⚠️ 永远不要传 `resume=None`**：`langgraph 1.2.15` 在 `langgraph/pregel/_loop.py:999/1014/1016` 处 `resume_is_map` 只在 `resume is not None` 分支被赋值却无条件使用 → `Command(resume=None)` **抛 `UnboundLocalError`**（我已独立核对源码确认）。**也永远不要传空 dict**（`Command(resume={})` 静默不恢复）。
3. **`interrupt()` 的返回值就是 resume 传入的值**；挂起时 interrupt 的 payload 可从 invoke 返回值的 `__interrupt__[0].value` 或 `get_state(cfg).tasks[0].interrupts` 读到。
4. **resume 会从该节点开头重跑**：`interrupt()` **之前**的代码每次恢复都会再执行一遍（实测副作用重复）。因此：
   - **`interrupt()` 之前不得有任何非幂等写操作**（不写库、不写事件、不落文件、不追加列表以外的东西）；
   - 需要"只执行一次"的逻辑放在 `interrupt()` **之后**；
   - **一个节点只放一个卡点**（同节点多个 interrupt 会让节点执行 N+1 次）。
5. **时间旅行可用**：`get_state_history(cfg)` 能拿到全部历史快照（实测 9 条）→ 这是 NFR-5「丢失版本 ≤1」的兑现方式。
6. **检查点必须放独立 SQLite 文件**（见 §3），不要放进 `data/mm.db`（避免污染 `profile_doc` 的不可变触发器语义，也避免与 `events` 写锁竞争）。

---

## 3. 设计规格（已裁定，按此实现）

### 3.1 检查点落位
- 独立库：默认 **`data/checkpoints.db`**（`data/*.db` 已在 `.gitignore` 内，**天然不会被提交**——这是选它而不选 `*.sqlite` 的原因之一）。
- 路径可覆盖：`Coordinator(ckpt_path=...)` 参数 + CLI `--ckpt`。
- 用 `SqliteSaver`（`langgraph.checkpoint.sqlite`）。连接建议 **只读复用** `mmagent.store.db.connect` 的配置思路（`busy_timeout=5000`、`check_same_thread=False`），但**不要修改 `store/db.py`**。

### 3.2 图结构（固定 10 节点，条件写在节点内）
```
START → intake → gate_caliber → profile → plan → solve → gate_sanity
      → render → assemble → gate_compliance → export → END
```
- 7 个阶段节点只负责推进 `stage` 与写最小状态；
- 3 个卡点节点各自**条件性**调用 `interrupt()`：条件不满足就 `return {}` 直接放行，**不要走条件边**（图结构保持静态，便于测试与审查）；
- `stage` 字段在每个节点里更新，终态 `DONE`。

### 3.3 状态 schema
用 `TypedDict`（优先 `typing_extensions.TypedDict` 或 stdlib）：
```
stage: str
caliber_conflicts: bool          # INTAKE 是否发现冲突
conflicts_confirmed: bool
sanity_blocking: bool
compliance_confirmed: bool
decisions: dict                  # gate -> 人工裁定原文
log: Annotated[list[str], operator.add]   # 追加语义
```
（不需要 `messages`。）

### 3.4 三个卡点的 payload 与裁定契约（**冻结，测试要断言**）
| 卡点 | gate 名 | 触发条件 | interrupt payload | resume 值（必需键 + 取值域） |
|---|---|---|---|---|
| 1 | `caliber_conflict` | `caliber_conflicts == True` 且未确认 | `{"gate":"caliber_conflict","stage":"INTAKE","question":...}` | `{"resolution": "use_mapping"\|"keep_raw"\|"human_override", "note": str}` |
| 2 | `sanity_blocking` | `sanity_blocking == True` | `{"gate":"sanity_blocking","stage":"SOLVE","question":...}` | `{"decision": "accept"\|"fix_and_rerun"\|"abort", "note": str}` |
| 3 | `pre_export_compliance` | 未确认合规 | `{"gate":"pre_export_compliance","stage":"ASSEMBLE","question":...}` | `{"decision": "approve"\|"reject", "note": str}` |

**payload 里必须带 `gate` 与 `stage` 两个键**（便于调用方判断该投什么裁定）。

### 3.5 输入校验（在调用 `Command` 之前做，防上游 bug）
`resume()` 必须先校验，不合法就抛一个**明确的自家异常**（建议 `InvalidGateDecision(Exception)`，带 gate 名与原因），**绝不把非法值传给 langgraph**：
- 拒绝 `None`
- 拒绝空 dict `{}`
- 拒绝非 dict
- 拒绝缺必需键
- 拒绝取值域外的值

### 3.6 对外接口（driver，由 `Coordinator` 提供）
```python
DEFAULT_CKPT_PATH = "data/checkpoints.db"

@dataclass
class RunStatus:
    run_id: str
    stage: str
    paused: bool
    gate: str | None          # paused=True 时必有
    question: str | None
    done: bool
    log: list[str]

class Coordinator:
    def __init__(self, db_path=DEFAULT_DB_PATH, ckpt_path=DEFAULT_CKPT_PATH): ...
    def start(self, run_id: str, context: dict) -> RunStatus: ...
    def resume(self, run_id: str, gate: str, decision: dict) -> RunStatus: ...
    def status(self, run_id: str) -> RunStatus: ...
```
- `thread_id` = **调用方传入的 `run_id`**（不要用 `trace_id`——它每次随机；也不要用 `profile_id`——卡点 1 时可能还没有）。
- `start()` 传初始 `context`（如 `{"caliber_conflicts": True, "sanity_blocking": False}`）驱动首次推演，停在卡点或跑到 `DONE`。
- `resume()` 校验 `gate` 必须等于当前挂起的 gate（不匹配则抛明确异常，避免投错卡点）。
- **保留** `Stage`、`MAX_CONSECUTIVE_FAILS`、`HumanGateRequired`、`CoordinatorState` 这些既有名字（作为共享词表）；`HumanGateRequired` 改为只用于**"同阶段连续失败 2 次 → 转人工"**这条升级路径（保留 `record_failure()`），三个 Graph 卡点走 interrupt/`RunStatus`，不再抛异常。**在模块 docstring 里写清这个分工。**

### 3.7 events 留痕（由 **driver** 写，不在节点内写）
在 driver 的 `start()/resume()` 里写 `events` 表（复用 `store.db.connect` + `utcnow`，沿用现有 `_emit` 风格）：
- `hitl.run_started`、`hitl.gate_paused`（含 gate/stage/question）、`hitl.gate_resumed`（含 gate/decision）、`hitl.run_finished`
- 仍保留 `coordinator.transition` 事件。
> 理由：resume 会重跑节点，写在节点内的留痕会重复。**这是硬约束。**

### 3.8 CLI 入口（DoD 必需）
在 `mmagent/cli.py` 增加 `run` 子命令组（沿用现有 argparse 风格）：
```
.venv\Scripts\python.exe -m mmagent.cli run start  --run-id demo-1 [--conflicts] [--sanity-blocking] [--ckpt data\checkpoints.db]
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-1 --gate caliber_conflict --decision '{"resolution":"use_mapping","note":"按建议"}' [--ckpt ...]
.venv\Scripts\python.exe -m mmagent.cli run status --run-id demo-1 [--ckpt ...]
```
- `run start` 命中卡点时 **exit 0** 并打印结构化 JSON（含 `paused/gate/question/run_id/stage`），不要用非零退出码表示"挂起"（挂起是正常状态）；
- `run resume` 投错 gate / 非法 decision → **exit 2** + 明确错误信息；
- `run status` 打印当前阶段与是否挂起。

### 3.9 顺带修一个真缺陷：`netguard-check` 未排除 `.agent-runs/`
`cli.py` 的 `_iter_repo_py` 只排除 `.venv/venv/__pycache__/.git`，而 `.agent-runs/` 已被 git 跟踪 35 项。调查员的探针实测把它打到 **EXIT=1**（探针里的 `import socket` 字样被当成产品代码）。
**修法**：把 `.agent-runs` 加入排除目录（与 `.venv` 同级），**不要放宽其他扫描规则**（真 `import socket` 仍须抓到）。修完自检：`netguard-check` 通过 EXIT=0，且**临时在仓库根写一个真 `import socket` 的文件仍被检出**（自检完删掉）。

---

## 4. 必须做 / 不要做

**做**：
- 重写 `mmagent/agents/coordinator.py`（保留 §3.6 的词表名）
- 更新 `mmagent/agents/__init__.py` 导出（如原本有导出）
- `mmagent/cli.py` 加 `run` 组 + netguard 排除
- `requirements.txt` 按实际 `pip freeze` 补 langgraph 全家（含 `langgraph==1.2.15`、`langgraph-checkpoint-sqlite==3.1.2`，以及 `langgraph-checkpoint==4.3.0`、`langgraph-prebuilt==1.1.0`、`langgraph-sdk==0.4.7`、`langchain-core==1.6.10`、`langsmith==0.14.8`、`aiosqlite==0.22.1`、`orjson==3.13.0` 等，以你 `pip freeze` 实测为准）。**注意 websockets 被 langgraph 从 17.2 拉到 16.1.1**，请显式写死并在报告中记录这条副作用。
- `README.md`：① 更正 Python 版本为 **3.13.14**；② 新增「人机回环（LangGraph）」小节：三卡点、状态图、`run start/resume/status` 命令示例、检查点库位置；③ 记录两条 **pitfall**：**永不传 `resume=None`**（上游 `UnboundLocalError`）与**「resume 会重跑节点、因此副作用必须放在 `interrupt()` 之后」**；④ 说明 `netguard-check` 已排除 `.agent-runs/`。

**不要做**（本轮明确出范围，只在报告里记为续作项）：
- 不要改 `mmagent/store/schema.sql`（含给 `events` 加不可变触发器 —— 需求 docx:666 有要求但会破冻结基线，留待队长决策）
- 不要改 `app.py` / `api/`（**本轮不加 HITL 的 HTTP 路由**，只做 CLI + 库接口）
- 不要动 `tests/`（Tess 的活）
- 不要动 FR-2/FR-5/FR-6/FR-9/FR-13 任何已验收逻辑
- 不要引入任何需要联网的依赖

---

## 5. 验收自检（**必须逐条跑并贴真实输出**）

1. 全量测试不回归：`pytest tests -q` → `189 passed, 1 xfailed`（与开工前一致）。
2. **同进程暂停/恢复**：`run start` 在卡点 1 挂起（exit 0，打印 gate=caliber_conflict）→ `run resume` 投 `{"resolution":"use_mapping"}` → `run status` 显示推进（若还有卡点则显示下一个）。
3. **跨进程恢复**（本轮核心）：`run start` 挂起 → **关闭进程** → 新进程 `run resume` → 能继续；把三次卡点依次投完，最终 `status` = `DONE`。**贴出两次调用的完整输出（含 run_id 与 stage 变化）。**
4. **非法输入被拦**：分别投 `None`、`{}`、非法字段值、投错 gate —— 四种都必须给出**明确自家异常/exit 2**，且**不得出现 langgraph 的 `UnboundLocalError`**。
5. **节点不重复执行副作用**：用一个可观测计数器（如每次进入卡点节点往 `events` 写一条）证明「同一卡点只留 1 条 `hitl.gate_paused`」，跨多次 resume 不重复膨胀。
6. netguard：`netguard-check` EXIT=0；且临时真 `import socket` 文件仍被检出（自检后清理该临时文件）。
7. `import` 冒烟：`mmagent.agents.coordinator`、`mmagent.cli` 均可导入；`data/checkpoints.db` 未被 git 跟踪（`git status --short` 里不出现）。
8. `git status --short` 里除你允许改的文件与 `.agent-runs/2026-10-14/` 外，**无其他改动**（尤其 `schema.sql`/`db.py`/`intake.py` 的 md5 必须不变）。

---

## 6. 报告要求

写入 `implementer-report.md`，含：
- 改动文件清单 + 每个文件**改了什么/为什么**
- §5 八条自检的**真实输出**
- **设计取舍记录**：为什么检查点放独立库、为什么 events 留痕放 driver、`HumanGateRequired` 与 Graph 卡点的分工
- **给 Tess 的交接提示**（她要写 `tests/test_nfr5_recovery.py`）：接口签名、异常类型、跨进程测试怎么做（建议 `subprocess` 起两次进程）、哪些地方最容易踩（`resume=None`、空 dict、节点重跑）
- **已知局限 / 未做项**（如实；禁止为过验收调弱断言）
- 改动后的 8 个基线文件 md5 复核结果（证明未漂移）

完成后回传**简短状态块**：改动文件、§5 自检通过情况、跨进程恢复是否实测通过、报告路径。**不要把报告全文倒回主会话。**
