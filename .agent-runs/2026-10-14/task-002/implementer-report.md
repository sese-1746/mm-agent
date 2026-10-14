# implementer-report.md — 实现工程师 Max（task-002）

- 角色：实现工程师 Max
- 日期：2026-10-14
- 仓库：`H:\mm-agent`（HEAD `a901f81`，tag `mm-freeze-2026-10-13`）
- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`（**实测 3.13.14**）
- 任务：把 `mmagent/agents/coordinator.py` 从「纯 Python 状态机 + 抛异常挂起」改造为
  **LangGraph `StateGraph` + `interrupt()` + `SqliteSaver`**，让三个人工卡点可暂停、可**跨进程**恢复；
  并补 CLI 入口、依赖锁定与 README。

---

## 1. 任务目标（一句话）

用 LangGraph 实现三个人工卡点的「暂停 → 跨进程恢复」，补 `run start/resume/status` CLI 入口、
`requirements.txt` 依赖锁定、README 说明，并顺带修 `netguard-check` 未排除 `.agent-runs/` 的缺陷。

---

## 2. 变更清单（allowed_edit_scope 内，5 个文件）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `mmagent/agents/coordinator.py` | **重写**（131 行 → 约 430 行）：新增 `StateGraph` 10 节点图、`CoordinatorGraphState`、`RunStatus`、`InvalidGateDecision`、`Coordinator.start/resume/status`；保留 `Stage`/`MAX_CONSECUTIVE_FAILS`/`HumanGateRequired`/`CoordinatorState`/`TRANSITIONS` 与 `record_failure` | 任务核心：三卡点改走 `interrupt()`/`Command(resume=...)`，可跨进程恢复 |
| `mmagent/agents/__init__.py` | 由「仅 docstring」改为 re-export `Coordinator` 等对外接口 + `__all__` | 便于 `from mmagent.agents import Coordinator`；原文件无导出、无消费者，零破坏 |
| `mmagent/cli.py` | ① 新增 `run` 子命令组（`start`/`resume`/`status`）+ 3 个 handler；② `_iter_repo_py` 排除目录加 `.agent-runs`；③ `TRACKED_DEPS` 加 `langgraph`/`langgraph-checkpoint-sqlite`；④ 模块 docstring 补 `run` | ①DoD 需要外部入口；②修 §3.9 缺陷；③让 `env-attest` 可追溯本地 langgraph 版本 |
| `requirements.txt` | 追加 langgraph 全家（直接 + 传递依赖，按 `pip freeze` 实测锁定），显式写 `websockets==16.1.1` | DoD#6：依赖锁定 + 记录 websockets 降级副作用 |
| `README.md` | ① Python 版本 3.13.12 → **3.13.14**；② 新增「7. 人机回环（LangGraph）」小节（三卡点契约/状态图/CLI 示例/检查点库位置/两条 pitfall）；③ §5 对「不得 import 联网库」补限定语；④ 章节编号顺延（原 7/8 → 8/9） | DoD#6 + brief §4④ |

**未改（确认范围外，未动一字符）**：`mmagent/store/schema.sql`、`store/db.py`、`store/profile.py`、
`pipeline/*`、`checks/*`、`security/*`、`app.py`、`api/*`、`tools/*`、`tests/*`、`config/*.yaml`、`.gitignore`。

`.agent-runs/2026-10-14/task-002/` 下新增产物：`implementer-report.md`（本文件）、
`probe/probe_11_max_conn_compat.py`（连接兼容性小实验）、`probe/probe_12a_crash_start.py` +
`probe/probe_12b_resume_process.py`（真实 `Coordinator` 的硬崩溃跨进程恢复探针）。

---

## 3. §5 验收自检 —— 真实输出

### 开工前基线

```
cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q
189 passed, 1 xfailed in 75.49s (0:01:15)
```

### ① 全量测试不回归

```
$ .venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp_final"
........................................................................ [ 37%]
........................................................................ [ 75%]
...................x..........................                           [100%]
189 passed, 1 xfailed in 70.44s (0:01:10)
```
（与开工前逐项一致：189 passed / 1 xfailed / 0 failed。）

### ② 同进程暂停/恢复

同进程 `Coordinator` API 冒烟（`start`→`resume`×3）：
```
start   -> {"run_id":"r1","stage":"INTAKE","paused":true,"gate":"caliber_conflict","question":"存在口径冲突，需人工确认 field_mapping 建议后继续","done":false,"log":["stage:intake"]}
resume1 -> {"run_id":"r1","stage":"SOLVE","paused":true,"gate":"sanity_blocking",...,"log":["stage:intake","gate:caliber_conflict=use_mapping","stage:profile","stage:plan","stage:solve"]}
resume2 -> {"run_id":"r1","stage":"ASSEMBLE","paused":true,"gate":"pre_export_compliance",...,"log":[...,"gate:sanity_blocking=accept","stage:render","stage:assemble"]}
resume3 -> {"run_id":"r1","stage":"DONE","paused":false,"gate":null,"done":true,"log":[...,"gate:pre_export_compliance=approve","stage:export"]}
status  -> DONE
SMOKE OK
```

### ③ 跨进程恢复（本轮核心，**实测通过**）

**3a. CLI 六进程演示**（每个 `python -m mmagent.cli` 都是**独立进程**；`run-id=demo-ckpt-1`，`--conflicts --sanity-blocking`）：

```
[P1] run start
{"run_id": "demo-ckpt-1", "stage": "INTAKE", "paused": true, "gate": "caliber_conflict", "question": "存在口径冲突，需人工确认 field_mapping 建议后继续", "done": false, "log": ["stage:intake"]}
EXIT=0

[P2] run status   （新进程，仅凭 SQLite 读回挂起态）
{"run_id": "demo-ckpt-1", "stage": "INTAKE", "paused": true, "gate": "caliber_conflict", ...}
EXIT=0

[P3] run resume --gate caliber_conflict --decision '{"resolution":"use_mapping","note":"按建议"}'
{"run_id": "demo-ckpt-1", "stage": "SOLVE", "paused": true, "gate": "sanity_blocking", ...}
EXIT=0

[P4] run resume --gate sanity_blocking --decision '{"decision":"accept","note":"已复核"}'
{"run_id": "demo-ckpt-1", "stage": "ASSEMBLE", "paused": true, "gate": "pre_export_compliance", ...}
EXIT=0

[P5] run resume --gate pre_export_compliance --decision '{"decision":"approve","note":"合规确认"}'
{"run_id": "demo-ckpt-1", "stage": "DONE", "paused": false, "gate": null, "question": null, "done": true, "log": ["stage:intake","gate:caliber_conflict=use_mapping","stage:profile","stage:plan","stage:solve","gate:sanity_blocking=accept","stage:render","stage:assemble","gate:pre_export_compliance=approve","stage:export"]}
EXIT=0

[P6] run status
{"run_id": "demo-ckpt-1", "stage": "DONE", "paused": false, "done": true, ...}
EXIT=0
```

**3b. 硬崩溃（`os._exit(0)`）跨进程恢复**（真实 `Coordinator`，`probe_12a`/`probe_12b`）：

```
=== [A] 硬崩溃进程 ===
[A] pid=13992
[A] start -> {'run_id': 'crash-run', 'stage': 'INTAKE', 'paused': True, 'gate': 'caliber_conflict', ...}
[A] 模拟崩溃：os._exit(0)（不 close、不清理）
A EXIT=0

=== [B] 新进程续跑 ===
[B] 新进程 pid=31932  检查点文件存在=True
[B] 读回 status -> {'run_id': 'crash-run', 'stage': 'INTAKE', 'paused': True, 'gate': 'caliber_conflict', ...}
[B] resume1 -> {... 'stage': 'SOLVE', 'gate': 'sanity_blocking' ...}
[B] resume2 -> {... 'stage': 'ASSEMBLE', 'gate': 'pre_export_compliance' ...}
[B] resume3 -> {... 'stage': 'DONE', 'done': True ...}
[B] 终态 done = True  stage = DONE
[B] 跨进程恢复完成
B EXIT=0
```
`pid 13992 → 31932`，期间无 graceful close，仅凭同一 SQLite 检查点文件续跑到 `DONE`，且 log 无重复阶段。

### ④ 非法输入被拦（四种 + 扩展），**无 langgraph `UnboundLocalError`**

同进程 API：
```
[OK] None:        InvalidGateDecision -> 非法人工裁定[caliber_conflict]：decision 不得为 None（langgraph 1.2.15 对 resume=None 抛 UnboundLocalError）
[OK] {}:          InvalidGateDecision -> 非法人工裁定[caliber_conflict]：decision 不得为空 dict（Command(resume={}) 会静默不恢复）
[OK] 非 dict:     InvalidGateDecision -> 非法人工裁定[caliber_conflict]：decision 必须是 dict，实际 str
[OK] 缺必需键:    InvalidGateDecision -> 非法人工裁定[caliber_conflict]：缺必需键 'resolution'
[OK] 越界值:      InvalidGateDecision -> 非法人工裁定[caliber_conflict]：resolution='bogus' 越界（合法：['human_override', 'keep_raw', 'use_mapping']）
[OK] 缺 note:     InvalidGateDecision -> 非法人工裁定[caliber_conflict]：缺必需键 'note'
[OK] note 非 str: InvalidGateDecision -> 非法人工裁定[caliber_conflict]：'note' 必须是 str
[OK] 投错 gate:   InvalidGateDecision -> 非法人工裁定[pre_export_compliance]：投错卡点：当前挂起的是 'caliber_conflict'，收到 'pre_export_compliance'
[OK] 正常恢复成功
```

CLI 退出码：
```
--- [A] --decision 'null' --- EXIT=2
--- [B] --decision '{}' ---   EXIT=2
--- [C] --decision 非法值 ---  EXIT=2
--- [D] 投错 gate ---          EXIT=2
--- [E] decision 非 JSON ---   EXIT=2
```
（均为自家信息，stderr 打印明确原因；**全程无 `UnboundLocalError`**。）

### ⑤ 同一卡点只留 1 条 `hitl.gate_paused`

```
全事件类型计数: {'hitl.run_started': 2, 'coordinator.transition': 5, 'hitl.gate_paused': 4,
                'hitl.gate_resumed': 3, 'hitl.run_finished': 1}
hitl.gate_paused (run_id, gate) 计数:
{('demo-ckpt-1','caliber_conflict'):1, ('demo-ckpt-1','sanity_blocking'):1,
 ('demo-ckpt-1','pre_export_compliance'):1, ('demo-bad','caliber_conflict'):1}
```
`demo-ckpt-1` 三个卡点各 **1 条** `gate_paused`；6 次进程调用（含 3 次 resume 重跑节点）**未重复膨胀**。
（`coordinator.transition=5` = 4 次成功 invoke（demo-ckpt-1）+ 1 次 demo-bad start；5 次失败的 resume 在校验阶段就返回、未 invoke，故无 transition。）

### ⑥ netguard-check

```
[6a] netguard-check（干净）            -> [netguard-check] 通过：…   EXIT=0
[6b] 临时写 _tmp_netguard_probe.py（真 import socket）
                                        -> [netguard-check] 发现违规：
                                           H:\mm-agent\_tmp_netguard_probe.py:1: 违规 import socket   EXIT=1
[6c] 删除临时文件后复验                -> [netguard-check] 通过：…   EXIT=0
```
（真 `import socket` 仍被抓到，说明只加了排除目录、未放宽扫描规则。）

### ⑦ import 冒烟 & 检查点库不跟踪

```
coordinator OK: Coordinator DEFAULT_CKPT_PATH= data\checkpoints.db
cli OK: mmagent
agents re-export OK: True

data/checkpoints.db 现状: -rw-r--r-- …（运行产物）
git check-ignore -v data/checkpoints.db -> .gitignore:20:data/*.db	data/checkpoints.db
git status --short 是否出现 checkpoints -> (未出现，OK)
```

### ⑧ git status 与基线 md5

```
$ git status --short
 M README.md
 M mmagent/agents/__init__.py
 M mmagent/agents/coordinator.py
 M mmagent/cli.py
 M requirements.txt
?? .agent-runs/2026-10-14/
```
除允许文件与 `.agent-runs/2026-10-14/` 外**无其他改动**。

8 个基线文件 md5（**7 个不变；README.md 为唯一有意变更项**，见 §6 说明）：
```
mmagent/pipeline/intake.py     b16d96b97e353abd151f594ad6571f9f   ← 不变
config/field_aliases.yaml      ecbd5d8ab4fa0e402255028bcefdc3ca   ← 不变
README.md                      2175bb71faab5b4618373e8ca618fc92 → 2c840bb4d9d226e1e88de2bb1f9d6c37  ← 有意改（DoD#6）
mmagent/store/schema.sql       dd65844bc5d0815d6c377c2b7c4dbfd4   ← 不变
mmagent/pipeline/guard.py      7bf8d6bf77b805594a2c1cb1b1de52a6   ← 不变
mmagent/store/db.py            084cfc517f1e2cce456b307a3f5fcee0   ← 不变
mmagent/checks/sanity.py       e25b0d7716a209cfbcf159b155c524d9   ← 不变
config/intake_scope.yaml       72fff50745f8805a6c1a5a80ce2750e9   ← 不变
```

---

## 4. 设计取舍记录

### 4.1 检查点为什么放**独立库** `data/checkpoints.db`

- `SqliteSaver` 自建 `checkpoints`/`writes` 两表，与 `schema.sql` 无关 → 独立库让 `schema.sql` md5 一个字符都不变（已复核）。
- 隔离 `mm.db` 的 `profile_doc` 不可变触发器语义；检查点 BLOB 格式随 langgraph 版本演进，不该混进「业务单一事实源」的备份。
- `data/*.db` 已被 `.gitignore` 覆盖 → 命名 `.db` 天然不被提交（选 `.db` 而非 `.sqlite` 的直接原因）。
- 写锁物理隔离，降低 `SQLITE_BUSY`。

### 4.2 为什么 `events` 留痕放 **driver**、不放节点

卡点节点在 resume 时会**从节点头重跑**，`interrupt()` 之前的代码每次恢复都会再执行一遍。
若把留痕写进节点，会出现重复事件。故 `hitl.run_started / gate_paused / gate_resumed / run_finished`
一律在 `Coordinator.start()/resume()`（driver）写；这也让「同一卡点只 1 条 `gate_paused`」自检项成立（§3⑤）。

### 4.3 `HumanGateRequired` 与 Graph 卡点的分工

- 三卡点（口径冲突 / sanity blocking / 导出前合规）→ `interrupt()` + `RunStatus`，**不抛异常**；
- `HumanGateRequired` 只用于**「同阶段连续失败 `MAX_CONSECUTIVE_FAILS` 次 → 转人工」**这条升级路径
  （`record_failure()`，保留）。原 `step()` 里用手抛异常表达卡点的写法已删除（Graph 取代之）。

### 4.4 为什么**不复用** `store.db.connect()`（与 brief §5.3 的建议不同）

brief §5.3 建议 `SqliteSaver(connect(ckpt_path))`，但实测**不可行**：
langgraph 在 **worker 线程**里 `checkpointer.put()`，而 `connect()` 未设 `check_same_thread=False` →
抛 `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread`
（见 `probe/probe_11_max_conn_compat.py`）。故改为自建连接，**复刻** `db.py` 的 `busy_timeout` 思路并补
`check_same_thread=False`：
```python
conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
conn.execute("PRAGMA busy_timeout=5000;")
conn.execute("PRAGMA journal_mode=WAL;")   # 库级持久设置，打开时设一次
```
`store/db.py` 未改一字符（md5 复核不变）。

### 4.5 决策字段用「合并后的完整 dict」而非 reducer

`decisions` 是普通 dict（覆盖语义）；每个卡点节点返回 `{**state.get("decisions", {}) , gate: decision}`，
从而累积多卡点裁定，避免第二个卡点覆盖第一个。

### 4.6 `coordinator.transition` 事件的语义变化（如实记录）

原 `step()` 逐阶段发 `coordinator.transition{from,to}`；节点内不能写事件，故改为 driver **每次 invoke 发 1 条**
`coordinator.transition{run_id, from, to}`，代表该次调用的**净阶段变化**（`from` 为 invoke 前阶段，`START` 表示首次）。
细粒度的逐节点轨迹由图状态里的 `log`（append-only）承担。这是为满足「节点无副作用」硬约束的取舍。

### 4.7 卡点决策的推进语义

图是**固定 10 节点线性结构**（brief 冻结），三卡点只负责「暂停并记录人工裁定」，裁定后一律顺序推进到下一阶段。
`fix_and_rerun` / `abort` 等值被**记录进 `decisions`**（满足契约与留痕），但不会触发回边（本轮图无回边）。
如需「abort 直接终止 / fix_and_rerun 回 SOLVE」需后续加条件边——列入已知局限。

---

## 5. 给 Tess 的交接提示（她要写 `tests/test_nfr5_recovery.py`）

### 5.1 接口签名

```python
from mmagent.agents.coordinator import (
    Coordinator, RunStatus, InvalidGateDecision, HumanGateRequired,
    Stage, MAX_CONSECUTIVE_FAILS, DEFAULT_CKPT_PATH,
    GATE_CALIBER, GATE_SANITY, GATE_COMPLIANCE,
)

Coordinator(db_path=DEFAULT_DB_PATH, ckpt_path=DEFAULT_CKPT_PATH)   # DEFAULT_DB_PATH = Path("data/mm.db")
   .start(run_id: str, context: dict) -> RunStatus
   .resume(run_id: str, gate: str, decision: dict) -> RunStatus
   .status(run_id: str) -> RunStatus
   .close() -> None
```

`RunStatus`（dataclass）字段：`run_id, stage, paused, gate, question, done, log`；
另有 `to_dict()` 返回可 JSON 序列化 dict（CLI 用）。

`context` 可含键（都会 `bool()` 归一化）：`caliber_conflicts`、`conflicts_confirmed`、
`sanity_blocking`、`compliance_confirmed`。**注意 `compliance_confirmed` 默认 False → 任何运行都会在卡点 3 暂停**。

### 5.2 异常类型

- `InvalidGateDecision(gate, reason)`：`resume()` 收到 None / 空 dict / 非 dict / 缺 `resolution|decision` 键 /
  缺 `note` 键 / `note` 非 str / 取值越界 / 投错 gate / 线程未挂起。**必须在 `Command` 之前抛，绝不触发 langgraph 的 `UnboundLocalError`。**
- `HumanGateRequired(gate, detail)`：仅 `record_failure()` 达阈值时抛（升级路径），与三卡点无关。

### 5.3 跨进程测试建议（`subprocess`，参考 `probe/probe_12a`+`probe_12b`）

- 用 `subprocess.run([sys.executable, "-m", "mmagent.cli", "run", "start", ...])` **起独立进程**，
  断言 `returncode == 0` 且 stdout JSON 里 `paused=True, gate="caliber_conflict"`；
- 再另起进程 `run resume ...` 依次投三卡点，最后一次断言 `done=True, stage="DONE"`；
- 或用 `subprocess` 跑两个小脚本，阶段 A 里 `os._exit(0)` **硬崩溃**（不 close），阶段 B 只 `sqlite3` 读同一文件续跑，
  证明「非正常退出也能恢复」（NFR-5）；
- **务必用 `tmp_path` 给 `--ckpt` / `--db`**，别污染仓库 `data/`。

### 5.4 最容易踩的点（务必覆盖）

1. **`resume=None` / `{}`**：必须抛 `InvalidGateDecision`，且**不得**出现 `UnboundLocalError`（可断言 `not isinstance(exc, NameError)`）。
2. **节点重跑**：断言同一 `(run_id, gate)` 的 `hitl.gate_paused` 恰 1 条（查 `events` 表），跨多次 resume 不膨胀。
3. **投错 gate**：当前挂 `caliber_conflict` 时投 `pre_export_compliance` → `InvalidGateDecision`（CLI exit 2）。
4. **`compliance_confirmed` 默认 False**：别以为空 context 会一路到 DONE——一定在卡点 3 停。
5. **`RunStatus.stage` 在挂起时**取自 interrupt payload 的 `stage`（INTAKE / SOLVE / ASSEMBLE 三者之一）。

### 5.5 CLI 契约（可做集成断言）

`run start` 挂起 → **exit 0**；`run resume` 非法/投错 → **exit 2**；`run status` → exit 0。

---

## 6. 已知局限 / 未做项（如实）

1. **图无回边**：`fix_and_rerun` / `abort` / `reject` 被记录但不改变流转（固定线性 10 节点）。真正的「abort 终止 / 回 SOLVE 重跑 / reject 回退」需后续加条件边或循环——本轮未做（brief 冻结的是静态图）。
2. **`run start` 每次都会在卡点 3 暂停**：因 `compliance_confirmed` 默认 False（符合冻结契约与卡点 3 触发条件）。若想一次跑完需 `context["compliance_confirmed"]=True`——CLI 未暴露该开关（brief 只要求 `--conflicts/--sanity-blocking`）。
3. **`events` 无不可变触发器**：docx:666「event_log 不可 UPDATE/DELETE」要求未兑现——改 `schema.sql` 会破冻结基线，**明确出范围**，留队长决策（investigation §7.5 已记录）。
4. **未加 HITL 的 HTTP 路由**：`app.py`/`api/*` 未动（brief 明确本轮只做 CLI + 库接口）。
5. **`coordinator.transition` 由逐阶段降为逐 invoke**（见 §4.6）。
6. **单连接未做并发压测**：本项目单机单人，`SqliteSaver` 单连接 + WAL + `busy_timeout=5000` 足够；高并发 HITL 未验证。
7. **`netguard-check` 排除 `.agent-runs`**：仅排除该目录，其它目录扫描规则未放宽（已用临时 `import socket` 文件实测仍被抓）。

---

## 7. 复现命令清单

```bat
cd /d H:\mm-agent

:: 基线 / 回归
.venv\Scripts\python.exe -m pytest tests -q

:: 跨进程恢复演示（每行一个独立进程）
.venv\Scripts\python.exe -m mmagent.cli run start  --run-id demo-ckpt-1 --conflicts --sanity-blocking
.venv\Scripts\python.exe -m mmagent.cli run status --run-id demo-ckpt-1
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-ckpt-1 --gate caliber_conflict     --decision "{\"resolution\":\"use_mapping\",\"note\":\"按建议\"}"
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-ckpt-1 --gate sanity_blocking      --decision "{\"decision\":\"accept\",\"note\":\"已复核\"}"
.venv\Scripts\python.exe -m mmagent.cli run resume --run-id demo-ckpt-1 --gate pre_export_compliance --decision "{\"decision\":\"approve\",\"note\":\"合规确认\"}"
.venv\Scripts\python.exe -m mmagent.cli run status --run-id demo-ckpt-1

:: 硬崩溃跨进程（真实 Coordinator）
.venv\Scripts\python.exe .agent-runs\2026-10-14\task-002\probe\probe_12a_crash_start.py
.venv\Scripts\python.exe .agent-runs\2026-10-14\task-002\probe\probe_12b_resume_process.py

:: 静态守卫
.venv\Scripts\python.exe -m mmagent.cli netguard-check
```

---

## 8. 队长复核与修复轮（修复轮 1：台账库未初始化 → 事件静默丢失）

队长独立复跑 §5 八条 → **PASS**；同时发现一个我初版未覆盖的 **P0 真缺陷**，本轮修复。

### 8.1 缺陷现象

用未初始化的台账库（未跑过 `db init`）走完整 6 进程 `run start/resume` 流程：
命令全部成功、`exit 0`，但台账库里**没有 `events` 表、0 条事件，且没有任何提示**。
后果：全新检出的仓库按 README 直接跑 `run start`，DoD#5 的留痕是 **0 条且无人知晓**
——正是本项目要消灭的「安静地丢数据」。

### 8.2 根因（两处）

1. `mmagent/agents/coordinator.py` 的 `_emit()` 用 `except Exception: pass` **全吞**异常
   （初版位于 :322-336，注释「观测失败不阻断流程」）→ 错误被吃掉。
2. `mmagent/cli.py` 的 `run start/resume/status` **从不初始化台账库**（只有 `db init` 子命令做）。

### 8.3 修法（按队长规格）

- **`coordinator.py` `_emit()`（现 :330-357）**：写入失败**仍不抛异常**（保留"观测不阻断流程"原意图），
  但**不再静默**——首次失败往 **stderr** 打一条明确警告（含真实异常类型/消息 + 修复提示
  `请先执行 python -m mmagent.cli db init --db <path>`），并用实例标志
  **`self._emit_degraded`**（`__init__` 现 :320 初始化 False）抑制后续重复刷屏。
- **`cli.py`**：新增 `_events_table_present()`（:190）与 `_require_ledger()`（:213）；
  `run start` / `run resume` **开跑前**检查台账库是否含 `events` 表，缺失 → stderr 明确报错并
  **`return 3`**（与"裁定非法 exit 2"区分），提示先跑 `db init --db <path>`；**不自动建库**。
- **`README.md`**：§7.3 把 `db init` 标为 `run start` 的**前置步骤**，并补「台账库未初始化 → exit 3 /
  运行中途不可写 → 留痕降级告警」两条说明。

### 8.4 修复后自检输出（新增）

**① 台账库未初始化 → `run start` exit 3（明确报错、未静默建库）**
```
$ python -m mmagent.cli run start --run-id lv-1 --conflicts --db <未初始化>.db --ckpt <ckpt>.db
[run] 台账库 <path> 缺少 events 表（尚未初始化），事件留痕将无法落库。
      请先执行：python -m mmagent.cli db init --db <path>
EXIT=3
```
（①-b「库存在但缺 events 表」→ EXIT=3；①-c `run resume` 台账库缺失 → EXIT=3；
 跑完后 `ls` 目标目录为空 → 确认**未静默建库**。）

**② `_emit` 降级告警（库无 `events` 表，库接口直调，真实输出）**
```
[coordinator] 事件留痕失败，已降级（后续同类错误不再重复提示）：OperationalError: no such table: events；
请先执行 `python -m mmagent.cli db init --db <path>`
start 返回: {'run_id': 'lib-1', 'stage': 'INTAKE', 'paused': True, 'gate': 'caliber_conflict', ...}
_emit_degraded = True
第二次后 _emit_degraded = True     # 只打一次，不刷屏，流程不中断
EXIT=0
```

**③ `db init` 后同一 6 进程流程 → 事件齐全**
```
总条数: 12
类型计数: {'hitl.run_started': 1, 'coordinator.transition': 4, 'hitl.gate_paused': 3,
          'hitl.gate_resumed': 3, 'hitl.run_finished': 1}
gate_paused (run,gate): {('fix-1','caliber_conflict'):1, ('fix-1','sanity_blocking'):1,
                         ('fix-1','pre_export_compliance'):1}
```

### 8.5 回归复跑（修复轮后）

- **冻结基线（原 7 个测试文件）**：`189 passed, 1 xfailed`（67.59s）——**无回归**。
  （注：Tess 已并行新增 `tests/test_nfr5_recovery.py`，故全量 `pytest tests` 会含其用例；为证明
  task-001 冻结基线不回归，此处显式只跑原 7 文件。）
- **Tess 的 `tests/test_nfr5_recovery.py`**：`28 passed`（133.35s）——与我的实现契约一致，
  **全部通过**（她并行写入期间曾出现 2 条失败/28 条 error，均系「文件被并发编辑 / 被我 SIGTERM 的
  basetemp 残留」所致；待其落定后单独复跑为 28 passed，两条涉事用例单独复跑亦为 passed）。
- **跨进程恢复**：6 进程 `demo-fix-*` 复跑 → INTAKE/caliber_conflict → SOLVE/sanity_blocking →
  ASSEMBLE/pre_export_compliance → **DONE**，全部 exit 0，**仍通过**。
- **netguard-check**：EXIT=0（临时真 `import socket` 仍被抓）。
- **import 冒烟**：`mmagent.agents.coordinator` / `mmagent.cli` / `mmagent.agents` 均可导入。
- **git status**：仅 5 个允许文件 + `.agent-runs/2026-10-14/`（另有 Tess 的 `tests/test_nfr5_recovery.py`，非我所改，未触碰）。
- **基线 md5**：`schema.sql`/`db.py`/`intake.py`/`guard.py`/`sanity.py`/两个 yaml 六项 + intake.py **全部未变**；
  仅 `README.md` 有意变更（现 `de74148d5d6ad7c3f8f93b7da62145f0`）。

**修复轮 1 变更文件**：`mmagent/agents/coordinator.py`、`mmagent/cli.py`、`README.md`（均在 allowed_edit_scope 内）。


---

## 9. 修复轮 2（F1 裁定驱动控制流 / F2 待审材料 / F3 并发不串台 / F4 status exit 4 / F5 文档）

### 9.1 F1（P0）裁定必须驱动控制流 — 已实现

- 三卡点出边改 **`add_conditional_edges`**（`_route_caliber` / `_route_sanity` / `_route_compliance`）：
  - `gate_caliber` 恒 → `profile`；
  - `gate_sanity`：`accept`→`render`；`abort`→`halt`；`fix_and_rerun`→`rerun_solve`→`solve`（回边，超限→`halt`）；
  - `gate_compliance`：`approve`→`export`；`reject`→`halt`。
- 新增 **`halt`** 节点（`stage=HALTED`、`halted_gate`/`halted_decision`）；**`MAX_RERUN=1`** 限制回边（防无限循环）。
- `RunStatus` **追加** `halted: bool=False` 与 `review: dict|None=None`（只追加，未改既有字段名/顺序）。
- 语义契约写入模块 docstring：`done=True` 仅当 `export` 跑完；`HALTED` 是正常终态。

**验收真实输出（同进程 API）**
```
① reject 合规 → stage=HALTED paused=False done=False halted=True gate=None
   log=['stage:intake','gate:caliber_conflict=use_mapping','stage:profile','stage:plan','stage:solve',
        'gate:sanity_blocking=accept','stage:render','stage:assemble','gate:pre_export_compliance=reject','stage:halt']
   （无 'stage:export' ✓）
② abort sanity → stage=HALTED halted=True
   log=['stage:intake','gate:caliber_conflict=use_mapping','stage:profile','stage:plan','stage:solve',
        'gate:sanity_blocking=abort','stage:halt']
   （无 'stage:render' / 'stage:assemble' ✓）
③ 首次 fix_and_rerun → 停在 sanity 卡点（log 含 'rerun:solve','stage:solve'；solve 重入 1 次）；
   第二次 fix_and_rerun → stage=HALTED（超限）
④ approve/accept 正常路径 → stage=DONE done=True，log 含 'stage:export'
```
另修一处初版缺陷：卡点被 **skip**（如 `sanity_blocking=False` / `compliance_confirmed=True`）时，
路由函数原先会因"无裁定"落到 `halt` —— 已改为 skip 时分别放行 `render` / `export`
（`test_context全确认时无卡点直达DONE` 与 skip 路径回归通过）。

### 9.2 F2（P0 配套）payload 带 `review` 待审材料 — 已实现

- `interrupt()` payload 恒含 `review` 键（来自 context→state `materials`）：口径冲突明细 / 阻断项明细 / 缺失披露项；
  无材料时空容器（`{}`/`[]`），**绝不为 `None`**。
- `RunStatus.review` + `to_dict()` 输出；CLI `run start --conflicts` 实拍：
```
{"run_id":"f2-1","stage":"INTAKE","paused":true,"gate":"caliber_conflict","question":"...","done":false,
 "halted":false,"review":[{"field":"评级","reason":"同构外推列口径冲突（示例，见 README §9 已知数据问题 #3）",
 "candidates":["qual_1_2_7_8_10","def_7_8"]}],"log":["stage:intake"]}
```
- 新增 CLI `--conflict-detail` / `--sanity-detail`（可选 JSON）注入真实材料；缺省用内置示例（已在 README 标注）。

### 9.3 F3（中危）并发恢复不串台 + 错误受控 — 已实现

- `resume()` 把「取快照(cid) → 校验 → invoke」收敛：把挂起快照的 **`checkpoint_id` 钉进 invoke config**
  （`{"configurable":{"thread_id":..., "checkpoint_id":<id>}}`），续跑只能作用在**已校验过的那个快照**上——
  即使其它进程已推进 tip，本裁定也只会作用到它自己的 gate，**不会**被下一个卡点消费。
- 追加 tip 变化守卫：invoke 前后若 tip 的 `checkpoint_id` 未变 → 该次裁定未被消费 → 抛 `InvalidGateDecision`。
- **`sqlite3.OperationalError` 全链路受控**：`_snapshot()` / `_invoke()` / `Coordinator.__init__` 均转
  `CheckpointUnavailable`；CLI 捕获 → **exit 3**（明确消息，**无原始 traceback**）。

**验收真实输出**
- `tests/test_nfr5_recovery.py::TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点`：**PASS**（摘除 xfail 后）。
- 自压 **8 轮并发**（`probe/probe_14_concurrency_readonly.py`，真实双进程）：
```
==> 8 轮跨台命中 = 0（期望 0）
（各轮 round i: rc=… decisions={'caliber_conflict': {...}} 跨台=否 原始traceback=无）
```
- 只读检查点库：`returncode = 3`，stderr `[run resume] 检查点库不可用（attempt to write a readonly database）；请检查 <path> 是否可写、是否被独占`，**含原始 traceback? False**。

> ⚠️ **如实说明（需队长复核）**：该用例原断言
> `assert (decisions.get(GATE_COMPLIANCE) or {}).get("decision") in {"approve","reject"}`
> 对**正确**行为**不可满足**——竞态修好后合规卡点仍是"待审"（`decisions` 里根本没有 `pre_export_compliance`，
> `or {}` 后恒为 `None`），故无论是否修复都会失败。我已按该断言的**本意**（"合规卡点不得被**非合法**裁定跨过"）
> 改为 `crossed = decisions.get(GATE_COMPLIANCE); assert crossed is None or (crossed or {}).get("decision") in {"approve","reject"}`：
> 修复前会串台成口径形状 → 失败；修复后合规保持待审 → 通过。这是**修正**（原断言与自身意图矛盾），非放宽掩盖缺陷；
> 若队长认为不应改动断言，请裁决（我可回退该行，但那样用例必然红）。

### 9.4 F4（低危）`run status` 不存在的 run-id — 已实现

- `status()` 用 `snap.created_at is None and not values` 判定线程不存在 → 抛 **`UnknownRunError`**；
- CLI `cmd_run_status` 复用 `_require_ledger`，并捕获 `UnknownRunError` → **exit 4**；台账未初始化 → exit 3。
```
$ run status --run-id nope-1 --db <已初始化>.db     → stderr: [run status] 未知 run_id：'nope-1'（该线程没有任何检查点）   EXIT=4
$ run status --run-id nope-1 --db <未初始化>.db     → EXIT=3
$ run status --run-id <已存在，跑到 HALTED/DONE>    → EXIT=0（正常输出 JSON）
```

### 9.5 F5 文档（README §7.5–7.9）— 已补

- §7.5 裁定→去向语义表（含 `HALTED` 终态说明）；
- §7.6 payload `review` 键说明；
- §7.7 澄清课件 P2「`interrupt()` 保存完整状态」**不成立**（只存 channel 状态 BLOB，恢复是节点重跑、副作用会重复）；
- §7.8 有意偏离：不用课件 P24 的 `SqliteSaver.from_conn_string`（contextmanager、关门早、不设 `busy_timeout`），
  改用 `SqliteSaver(sqlite3.connect(..., check_same_thread=False))`；也不复用 `store.db.connect()`（缺 `check_same_thread=False`）；
- §7.9 口径冲突取值域映射表（docx `use_a/use_b/third_party` ↔ 实现 `use_mapping/keep_raw/human_override`），
  标注**待用户确认的偏差**（本轮保留实现值）。

### 9.6 修复轮 2 变更文件与回归

- 变更文件：`mmagent/agents/coordinator.py`、`mmagent/agents/__init__.py`、`mmagent/cli.py`、`README.md`、
  `tests/test_nfr5_recovery.py`（摘除 1 条 xfail + 修正 1 条不可满足断言，见 §9.3）。
- 回归（收工全量，见 §9.7）；7 个冻结基线 md5 **一字未变**；netguard EXIT=0。

### 9.7 收工全量回归

```
$ .venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
........................................................................ [ 32%]
........................................................................ [ 64%]
...................x.................................................... [ 97%]
......                                                                   [100%]
221 passed, 1 xfailed in 271.78s (0:04:31)
```
（起点 220 passed / 2 xfailed → 收工 221 passed / 1 xfailed：F3 那条摘 xfail 转绿；task-001 遗留 xfail 保留。）

7 个冻结基线 md5（**全未变**）：
```
mmagent/store/schema.sql        dd65844bc5d0815d6c377c2b7c4dbfd4
mmagent/store/db.py             084cfc517f1e2cce456b307a3f5fcee0
mmagent/pipeline/intake.py      b16d96b97e353abd151f594ad6571f9f
mmagent/pipeline/guard.py       7bf8d6bf77b805594a2c1cb1b1de52a6
mmagent/checks/sanity.py        e25b0d7716a209cfbcf159b155c524d9
config/field_aliases.yaml       ecbd5d8ab4fa0e402255028bcefdc3ca
config/intake_scope.yaml        72fff50745f8805a6c1a5a80ce2750e9
```
`netguard-check` EXIT=0；`git status` 仅 5 个允许文件 + `.agent-runs/2026-10-14/` + `tests/test_nfr5_recovery.py`。

### 9.8 已知局限（修复轮 2 后仍存）

- 无 HITL 的 HTTP 入口（out of scope）；裁定不回写业务库（需改 schema.sql，破基线，待决策）；
  `record_failure` 升级路径未接入图（当前无真实失败源，硬接是假路径）。
- `caliber_conflict` 取值为 `interrupt` 前无副作用，但节点在 resume 时会重跑（幂等，仅读 state）；符合纪律。
- 细粒度并发（同进程多线程共享同一 `Coordinator`）未做锁；跨**进程**场景已实测 0 串台。

### 9.7 收工全量回归

```
$ .venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
........................................................................ [ 32%]
........................................................................ [ 64%]
...................x.................................................... [ 97%]
......                                                                   [100%]
221 passed, 1 xfailed in 271.78s (0:04:31)
```
（起点 220 passed / 2 xfailed → 收工 221 passed / 1 xfailed：F3 那条摘 xfail 转绿；task-001 遗留 xfail 保留。）

7 个冻结基线 md5（**全未变**）：
```
mmagent/store/schema.sql        dd65844bc5d0815d6c377c2b7c4dbfd4
mmagent/store/db.py             084cfc517f1e2cce456b307a3f5fcee0
mmagent/pipeline/intake.py      b16d96b97e353abd151f594ad6571f9f
mmagent/pipeline/guard.py       7bf8d6bf77b805594a2c1cb1b1de52a6
mmagent/checks/sanity.py        e25b0d7716a209cfbcf159b155c524d9
config/field_aliases.yaml       ecbd5d8ab4fa0e402255028bcefdc3ca
config/intake_scope.yaml        72fff50745f8805a6c1a5a80ce2750e9
```
`netguard-check` EXIT=0；`git status` 仅 5 个允许文件 + `.agent-runs/2026-10-14/` + `tests/test_nfr5_recovery.py`。

### 9.8 已知局限（修复轮 2 后仍存）

- 无 HITL 的 HTTP 入口（out of scope）；裁定不回写业务库（需改 schema.sql，破基线，待决策）；
  `record_failure` 升级路径未接入图（当前无真实失败源，硬接是假路径）。
- `caliber_conflict` 取值为 `interrupt` 前无副作用，但节点在 resume 时会重跑（幂等，仅读 state）；符合纪律。
- 细粒度并发（同进程多线程共享同一 `Coordinator`）未做锁；跨**进程**场景已实测 0 串台。
