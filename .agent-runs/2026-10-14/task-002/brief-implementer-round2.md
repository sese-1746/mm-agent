# brief-implementer-round2.md — 实现工程师 Max（task-002 修复轮 2）

> 本文件是**修复轮 2 的完整规格**。上一轮规格只通过消息下发，会话中断后丢失，故落盘为准。
> 项目：`H:\mm-agent`｜解释器：`.venv\Scripts\python.exe`（Python 3.13.14）｜任务目录：`.agent-runs\2026-10-14\task-002\`

---

## 起点（已核实，2026-10-14 18:03）

- 全量测试：**220 passed / 2 xfailed / 0 failed**（`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q`）
  - 2 条 xfail = task-001 遗留 1 条（`test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`，**不要动**）+ 本轮 F3 要修的那 1 条（`test_nfr5_recovery.py::TestConcurrentResumeRace::...`）
- **修复轮 2 目前零落地**（队长已核实）：`coordinator.py` 无 `add_conditional_edges`、无 `halt` 节点、无 `MAX_RERUN`、payload 无 `review`；`cli.py` 无 `exit 4`；`checkpoint_id` / `OperationalError` 无处理；`tests/test_nfr5_recovery.py:346` 的 xfail 标记仍在。

---

## 两条待修缺陷（队长均已独立复现，证据在下）

### 缺陷 1（P0）：人工裁定只被"记录"、不被"消费"
队长用真实 CLI 复现：
```
resume(gate=pre_export_compliance, {"decision":"reject"})
  → stage=DONE, done=True
  → log: [..., 'gate:pre_export_compliance=reject', 'stage:export']   ← 否决后仍执行了 export

resume(gate=sanity_blocking, {"decision":"abort"})
  → stage=ASSEMBLE
  → log: [..., 'gate:sanity_blocking=abort', 'stage:render', 'stage:assemble']  ← 中止后仍执行了 render
```
根因：
- `coordinator.py:218-231` `_node_gate_compliance` 无论 approve/reject 都返回 `compliance_confirmed=True`
- `coordinator.py:195-207` `_node_gate_sanity` 同理，不区分 accept / fix_and_rerun / abort
- `coordinator.py:238-263` 图构建**全部是 `add_edge`，没有任何 `add_conditional_edges`**；也没有任何阶段节点读 `decisions`
危害：审计显示"已否决"而产物照导出，**比没有卡点更危险**；违反需求 docx:443「导出前校验缺失即阻断」。

### 缺陷 2（中危）：并发恢复 TOCTOU → 合规卡点 fail-open
Tess 实测 8 轮 **6 轮命中**：`resume()` 的「读状态 → invoke」无锁、未钉快照，两进程同时恢复同一挂起点时，后到者的 `Command(resume=...)` 被应用到**下一个**卡点的 interrupt 上 → **合规卡点被一个对其非法的裁定静默跨过并跑到 DONE**（终态 `decisions['pre_export_compliance']` 是口径形状的值，`log` 出现 `gate:pre_export_compliance=None`）。
另有 1 次 `sqlite3.OperationalError: attempt to write a readonly database` **未受控退出 rc=1 + 原始 traceback**。

---

## F1（P0）裁定必须驱动控制流

1. 三卡点节点把裁定写入 state（沿用 `decisions`）。
2. 三条出边改 **`add_conditional_edges`**：
   - `gate_caliber`：口径冲突只有"如何解决"语义，合法裁定后 → `profile`。
   - `gate_sanity`：`accept` → `render`；**`abort` → `halt`**（不得再进 `render`/`assemble`）；`fix_and_rerun` → 回 `solve`（回边）。
   - `gate_compliance`：`approve` → `export`；**`reject` → `halt`**（不得再进 `export`，更不得 `DONE`）。
3. 新增 **`halt` 节点**：`stage` 置 `Stage.HALTED`，写事件 **`hitl.run_halted`**（payload 含 gate + 裁定原文），→ `END`。
4. **回边必须设迭代上限**：`fix_and_rerun` 重入 `solve` 最多 **1 次**（常量 `MAX_RERUN = 1`），超限 → `halt`。
   依据：课件 `07_Graph_Workflow 架构` P10 L786 明确警告条件路由设计缺陷会造成**无限循环**，必须给阈值。
5. `RunStatus` **追加字段 `halted: bool = False`**（只追加，不改已有字段名/顺序，避免打断 Tess 的断言）。`halt` 时：`stage="HALTED"`、`done=False`、`paused=False`、`halted=True`。
6. 语义契约写进模块 docstring：**`done=True` 仅当 `export` 节点跑完**；`HALTED` 是正常终态而非异常。

**验收（贴真实输出）**
- ① `resume(pre_export_compliance, {"decision":"reject"})` → `stage=HALTED`、`done=False`、`halted=True`，**log 中不得出现 `stage:export`**
- ② `resume(sanity_blocking, {"decision":"abort"})` → `HALTED`，**log 中不得出现 `stage:render` / `stage:assemble`**
- ③ `fix_and_rerun` → `solve` 只重入一次，二次触发上限后转 `halt`
- ④ approve/accept 正常路径仍跑到 `DONE`（回归）

---

## F2（P0 配套）卡点 payload 必须带"待审内容"

现状：payload 只有静态常量 `{gate, stage, question}`（`GATE_QUESTION` 在 `coordinator.py:93-97`），人工**看不到该审什么**。

- `interrupt()` payload 增加 **`review` 键**，携带 context 里的待审材料：
  - `gate_caliber` → 口径冲突明细
  - `gate_sanity` → 阻断项明细
  - `gate_compliance` → 缺失披露项（如 `missing_disclosure`）
- 键**必须始终存在**；无材料时用**空容器**（`{}`/`[]`），**不要 `None`**。
- `RunStatus` 追加 `review: dict | None = None`；CLI `run start`/`run status` 的 stdout JSON 要能看到它。

**验收**：`run start --conflicts` 挂起时，stdout JSON 的 `review` 里能看到冲突明细内容。

---

## F3（中危）并发恢复不得串台 + 错误必须受控

- `resume()` 把「读状态 → 校验 → invoke」收敛到**同一快照**：读出当前挂起快照的 `checkpoint_id`，钉进 invoke 的 config（`{"configurable": {"thread_id": ..., "checkpoint_id": <id>}}`），使续跑只能从**已校验过的那个快照**发生；快照已过期/不存在 → 抛明确的自家异常 → CLI **exit 2**。
- 机制可自行调整，但**硬指标是：陈旧裁定不得被应用到下一个卡点**，并用 Tess 的用例实测证明。
- **任何 `sqlite3.OperationalError`**（含 `attempt to write a readonly database`）必须转成受控错误（明确消息 + exit 2/3），**禁止 rc=1 + 原始 traceback**。

**验收**
- `tests/test_nfr5_recovery.py::TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点` **摘除 `xfail` 标记后必须 PASS**（队长授权你摘这一条标记，并在报告中说明）
- 自己再压 **8 轮并发**：跨台命中 = 0；readonly 场景受控退出

---

## F4（低危）`run status` 对不存在的 run-id

现状：`run status --run-id <不存在>` 静默 **exit 0** 且 `stage=""`（`coordinator.py:357-378` 未区分「线程不存在」vs「存在但未挂起」；`cli.py:269-279` 的 `cmd_run_status` 未复用 `_require_ledger`）。

- 区分二者：不存在 → 明确错误 + **`exit 4`**（2 = 裁定非法 / 3 = 台账未初始化 / 4 = run_id 不存在）
- `cmd_run_status` 复用 `_require_ledger`

**验收**：贴真实输出（含 exit code）。

---

## F5 文档（README.md）

1. **裁定→去向语义表**：approve / accept / reject / abort / fix_and_rerun 各自流转 + `HALTED` 终态说明
2. payload 的 **`review` 键**说明
3. **澄清课件错误表述**：HITL 课件 P2 称「`interrupt()` 保存完整状态（包括内存、文件、网络连接）」——实测**不成立**（只存 channel 状态 BLOB，恢复是节点从头重跑、副作用会重复），补一段避免后人误用
4. **标注有意偏离**：HITL 课件 P24 推荐 `SqliteSaver.from_conn_string("xxx.db")`，本项目用 `SqliteSaver(sqlite3.connect(...))`。前者是 contextmanager、yield 完即关连接且不设 `busy_timeout`，**不适合长驻实例 + 跨进程恢复**——写清"刻意不用"，别被后人照课件改回去
5. **口径冲突裁定值域映射表**：docx 用 `use_a`/`use_b`/`third_party`，实现用 `use_mapping`/`keep_raw`/`human_override`。队长裁定**本轮保留实现值**，但必须列出映射并标注「待用户确认的偏差」

→ 并在 `implementer-report.md` 追加「修复轮 2」章节。

---

## 明确不做（只在报告记录，别动手）

- HITL 的 HTTP/FastAPI 入口（本轮 out of scope）
- 裁定回写业务库（需改 `schema.sql` 加 `caliber_conflict`/`field_mapping` 表 → 破冻结基线，待用户决策）
- 把 `record_failure` 升级路径接入图（阶段节点当前无真实失败源，硬接是假路径）

---

## 约束与自检

- **allowed_edit_scope**：`mmagent/agents/coordinator.py`、`mmagent/agents/__init__.py`、`mmagent/cli.py`、`requirements.txt`、`README.md`、`tests/test_nfr5_recovery.py`（**仅摘除那一条 xfail 标记**）、`.agent-runs/2026-10-14/task-002/` 下报告。
  **禁止**动：`mmagent/store/*`、`mmagent/pipeline/*`、`mmagent/checks/*`、`mmagent/security/*`、`mmagent/app.py`、`mmagent/api/*`、其他测试文件、`config/*.yaml`、`.gitignore`。
- 收工全量测试期望：**221 passed / 1 xfailed / 0 failed**。
- 冻结基线 7 个文件 md5 **必须一字不变**：`schema.sql`、`db.py`、`intake.py`、`guard.py`、`sanity.py`、`config/field_aliases.yaml`、`config/intake_scope.yaml`（`README.md` 会有意变更，属预期）。
- **禁止调弱断言或伪造成功**；跑不通就贴真实失败输出与根因。
- 若某项确实做不完，如实报告「未完成 + 卡在哪」，不要留半成品中间态。

## 回传

简短状态块：改动文件、F1~F4 各自验收的真实输出（尤其 ①② 的 log，必须能看出没有 `stage:export`）、全量测试计数、7 个基线文件 md5 复核、报告路径。**不要倒全文。**
