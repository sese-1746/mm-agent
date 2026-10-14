# test-report.md — 测试工程师 Tess（task-002）

- 角色：测试工程师 Tess
- 日期：2026-10-14
- 仓库：`H:\mm-agent`（HEAD `a901f81`）
- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`（Python 3.13.14）
- 被测实现（md5，测试期间未变）：
  - `mmagent/agents/coordinator.py` `b2f64ea539927ab1ec90391500912a8c`（mtime 17:07:22）
  - `mmagent/cli.py` `83319b490a6192b596bef4b37128b596`（mtime 17:07:30）
- 新增用例文件：`tests/test_nfr5_recovery.py`（**32 个用例**；md5 `395c52b071db96ce5469d3e506410135`；`mmagent/` 未改一字符）
- 运行命令：`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q -rxX`

---

## 0. 结论（先说最重要的一句）

**NFR-5「跨进程恢复」有测试用例支撑，且证据是真实跨进程执行。**
挂起态的恢复**不依赖任何进程内内存**：每条恢复步骤都是独立的 `python` 子进程（实测 6 个互不相同的 pid，
且都不等于 pytest 进程 pid），仅凭同一个 SQLite 检查点库续跑到 `DONE`；
`os._exit(0)` 硬崩溃（不 close、不清理 WAL）后同样能续跑到 `DONE`。

但**发现 1 个真缺陷**：`resume()` 的「读状态 → invoke」之间存在 TOCTOU 竞态窗口，
两个进程同时恢复同一挂起点时，**合规卡点（`pre_export_compliance`）会被一个对其非法的裁定值静默跨过**
（fail-open）。已固化为**确定性** xfail 用例（`ISSUES-NFR5-01`）。这不影响 NFR-5 的恢复能力，
但影响卡点契约的完整性。另有 1 个低危观察项（`ISSUES-NFR5-02`）。

---

## 1. 用例清单（文件 + 用例名 + 覆盖需求编号）

文件：`tests/test_nfr5_recovery.py`（32 用例 = 31 passed + 1 xfailed）

| brief 条目 | 需求 | 用例（节点名） | 意图 |
|---|---|---|---|
| A1 | NFR-5 | `TestCrossProcessRecovery::test_五个独立进程依次投递三卡点最终到达DONE` | 6 个独立进程：start 挂起 → status（只读回挂起态）→ 3 次 resume → status 断言 DONE |
| A2 | NFR-5 | `TestCrossProcessRecovery::test_硬崩溃os_exit后新进程仍能恢复至DONE` | 进程 A 挂起后 `os._exit(0)` 硬崩（不 close、不清理 WAL）→ 新进程续跑到 DONE |
| A3 | NFR-5 | `TestCrossProcessRecovery::test_时间旅行_get_state_history可回溯到挂起那一刻` | 快照数 ≥ 已推进节点数，且能取到挂起那一刻的 interrupt payload |
| A+（并发边界） | NFR-5 / FR-5 | `TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点` | **xfail**：确定性撑开 TOCTOU 窗口，断言合规卡点不被非法值跨过（`ISSUES-NFR5-01`） |
| B4 | FR-5 | `TestGateContract::test_三卡点各自用合法裁定可恢复[卡点1口径冲突 / 卡点2合理性阻断 / 卡点3导出前合规]` | 三个 gate 各自用域内裁定恢复成功 |
| B4 | FR-5 | `TestGateContract::test_三卡点全流程log顺序严格正确` | `log` 逐项等于契约规定的 10 条序列 |
| B5 | FR-5 | `TestGateContract::test_paused与done状态不变式` | 每个挂起点 `gate`/`question` 非空；`done=True` 时 `paused=False` 且 `gate is None` |
| B5+（边界） | FR-5 | `TestGateContract::test_context全确认时无卡点直达DONE` | `compliance_confirmed=True` → 三卡点全 skip 直达 DONE |
| C6 | FR-5 | `TestInvalidDecisionRejected::test_非法裁定被拦截且状态不被污染[None / 空dict / 非dict-str / 非dict-list / 缺resolution键 / resolution越界 / 缺note键 / note非str]` | 8 种非法值全部 `InvalidGateDecision`，**不触发 langgraph `UnboundLocalError`**，状态不被污染且不新增留痕 |
| C6（边界） | FR-5 | `TestInvalidDecisionRejected::test_未知卡点名被拦`、`::test_start收到非dict_context被拦` | 未知 gate 名 / 非 dict context |
| C7 | FR-5 | `TestInvalidDecisionRejected::test_投错gate被拦且状态不被污染` | 挂 `sanity_blocking` 时投 `caliber_conflict` → 被拦，状态仍在原卡点，随后仍能正确恢复 |
| C7（边界） | FR-5 | `TestInvalidDecisionRejected::test_未挂起线程投裁定被拦` | 跑到 DONE 后再投 → 明确报「当前未挂起」 |
| C8 | FR-5 | `TestInvalidDecisionRejected::test_CLI非法裁定返回码2` | 6 种非法 CLI 裁定 → `returncode == 2` + stderr 明确原因；之后合法裁定仍能恢复 |
| D9 | FR-5 / 留痕 | `TestEventLedger::test_同一卡点hitl_gate_paused恰好一条` | 同一 `(run_id, gate)` 恰好 1 条（3 个 gate 各 1），额外 status/失败 resume 不膨胀 |
| D9 | FR-5 / 留痕 | `TestEventLedger::test_跨进程续跑也不重复留痕` | 跨进程 resume 场景下同样不膨胀 |
| D10 | FR-5 / 留痕 | `TestEventLedger::test_事件次数与投递次数一致` | `run_started==1`、`gate_resumed==投递次数==3`、`gate_paused==3`、`run_finished==1` |
| E11 | NFR-5 | `TestLedgerNotInitialized::test_台账库不存在时CLI_start退出码3并提示db_init` | exit 3 + stderr 指向 `db init --db <path>`；**不静默建库、不输出成功 JSON** |
| E11 | NFR-5 | `TestLedgerNotInitialized::test_台账库存在但缺events表时CLI_start退出码3` | 空库（无 `events` 表）同样 exit 3 |
| E11 | NFR-5 | `TestLedgerNotInitialized::test_台账库未初始化时CLI_resume同样退出码3` | resume 路径同样受台账前置校验保护 |
| 附加（升级路径回归） | FR-5 | `TestEscalationPath::test_未达阈值只累计不转人工` / `::test_同阶段连续失败达阈值转人工并置HALTED` / `::test_人机回环卡点不抛HumanGateRequired` | `HumanGateRequired`（同阶段连续失败达阈值）与三卡点两条路径互不混淆 |
| F12 | 回归 | 不写成用例（见 §2.2 / §2.3） | 全量回归不退化 |

**分类计数**：`TestInvalidDecisionRejected` 13 / `TestGateContract` 6 / `TestCrossProcessRecovery` 3 /
`TestEventLedger` 3 / `TestLedgerNotInitialized` 3 / `TestEscalationPath` 3 / `TestConcurrentResumeRace` 1 = **32**。

---

## 2. 运行结果（真实输出）

> 命令：`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest ...`

### 2.1 新增用例文件

```
$ .venv\Scripts\python.exe -m pytest tests/test_nfr5_recovery.py -q -rxX
...x............................                                         [100%]
=========================== short test summary info ===========================
XFAIL tests/test_nfr5_recovery.py::TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点 - resume() 的「读状态(coordinator.py:456) → invoke(coordinator.py:467)」之间无锁保护；……（完整 reason 见 §2.4）
31 passed, 1 xfailed in 186.39s (0:03:06)
```

**E 组（台账库未初始化）已全绿**：`TestLedgerNotInitialized` 3 条一次通过，
未出现「先红后绿」的顺序 —— 实现方的修复（`cli.py:213-221` `_require_ledger`，mtime 17:07:30）
早于我首次运行（17:10），故本轮无需等待。

### 2.2 task-001 基线（排除新增文件）——**无退化**

```
$ .venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_nfr5_recovery.py
........................................................................ [ 37%]
........................................................................ [ 75%]
...................x..........................                           [100%]
189 passed, 1 xfailed in 77.91s (0:01:17)
```

### 2.3 全量

```
$ .venv\Scripts\python.exe -m pytest tests -q
...................x.....x.............................................. [ 97%]
......                                                                   [100%]
220 passed, 2 xfailed in 265.75s (0:04:25)
```

**计数口径（三份数字完全自洽）**：

| 口径 | passed | xfailed | failed | skipped |
|---|---|---|---|---|
| task-001 基线（`--ignore` 新增文件） | **189** | **1** | 0 | 0 |
| 本轮新增文件 `test_nfr5_recovery.py` | **31** | **1** | 0 | 0 |
| 全量 `pytest tests -q` | **220** = 189 + 31 | **2** = 1 + 1 | **0** | **0** |

- 全量的 1 条 xfail 是 task-001 遗留的 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`（未动）；
- 另 1 条 xfail 是本轮**新发现**的并发缺陷（`ISSUES-NFR5-01`，见 §2.4），已按 brief「对真缺陷标 xfail 并附证据」处置；
- **新增 failed = 0、skipped = 0**，task-001 的 190 例无退化。

### 2.4 新增 xfail 明细（`-rxX`）

```
XFAIL tests/test_nfr5_recovery.py::TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点
      resume() 的「读状态(coordinator.py:456) → invoke(coordinator.py:467)」之间无锁保护；……
```

强制运行（`--runxfail`）得到的**真实失败输出**：

```
$ .venv\Scripts\python.exe -m pytest "tests/test_nfr5_recovery.py::TestConcurrentResumeRace" -q --runxfail
>           assert compliance.get("decision") in {"approve", "reject"}, (
                f"合规卡点不得被非合法裁定跨过，实际 decisions={decisions}")
E               AssertionError: 合规卡点不得被非合法裁定跨过，实际 decisions={'caliber_conflict':
                {'resolution': 'use_mapping', 'note': '按建议口径合并'},
                'pre_export_compliance': {'resolution': 'use_mapping', 'note': '按建议口径合并'}}
E               assert None in {'approve', 'reject'}
1 failed in 3.93s
```

> ⚠️ **状态更新（修复轮 2）**：该缺陷已被修复（`resume()` 用 `checkpoint_id` 钉住已校验快照），
> xfail 标记已摘除，用例现为 **PASS**。详见 §8（修复轮 2 回归）。

---

## 3. 跨进程证据（真实子进程输出片段，可看出是两个不同 pid）

命令：`pytest "tests/test_nfr5_recovery.py::TestCrossProcessRecovery" -q -s`
（用例内 `print` 出每个子进程**自报**的 pid，并断言 `pid != os.getpid()` 且「至少 2 个不同 pid」）

### 3.1 六个独立进程投递三卡点（A1）

```
[XP] P1 start   pid=14748 rc=0 {"run_id": "xp-full-1", "stage": "INTAKE", "paused": true, "gate": "caliber_conflict", "question": "存在口径冲突，需人工确认 field_mapping 建议后继续", "done": false, "log": ["stage:intake"]}
[XP] P2 status  pid=34368 rc=0 paused=True gate=caliber_conflict
[XP] P3 resume pid=37992 rc=0 paused=True gate=sanity_blocking stage=SOLVE
[XP] P4 resume pid=30408 rc=0 paused=True gate=pre_export_compliance stage=ASSEMBLE
[XP] P5 resume pid=31416 rc=0 paused=False gate=None stage=DONE
[XP] P6 status  pid=14204 rc=0 done=True stage=DONE
[XP] pytest pid=35504 child pids=[14748, 34368, 37992, 30408, 31416, 14204]
```

- 6 个子进程 pid **互不相同**，且**均不等于** pytest 进程 pid（`35504`）→ 每步都是全新进程；
- P2 只做 `status`（不推进图）就正确读回「挂起在 `caliber_conflict`」→ 状态来自 SQLite 检查点库，
  **进程间无内存传递**；
- P6 的 `log` 与单进程跑完的三卡点序列逐项相等（用例内 `assert s6["log"] == FULL_LOG_WITH_THREE_GATES`）。

### 3.2 硬崩溃（`os._exit(0)`）后新进程续跑（A2）

```
[XP-CRASH] A pid=25740 rc=0 paused=True gate=caliber_conflict
[XP-CRASH] resume caliber_conflict: pid=31008 rc=0 stage=SOLVE paused=True gate=sanity_blocking
[XP-CRASH] resume sanity_blocking: pid=35912 rc=0 stage=ASSEMBLE paused=True gate=pre_export_compliance
[XP-CRASH] resume pre_export_compliance: pid=4200 rc=0 stage=DONE paused=False gate=None
[XP-CRASH] final pid=6736 done=True stage=DONE
```

崩溃进程 `25740` 与后续 4 个进程（`31008 / 35912 / 4200 / 6736`）均为**不同 pid**；
`os._exit(0)` 未执行 `close()`、未清理 `-wal`，新进程仅凭同一 `ckpt.db` 续跑到 `DONE`，
且 `log` 无重复阶段（`assert status["log"] == FULL_LOG_WITH_THREE_GATES`）。

### 3.3 隔离自检：不污染运行态库

跨进程用例全部通过 `pytest tmp_path` 传 `--ckpt` / `--db`；跑 3 组代表性用例前后，
仓库运行态库 mtime **未变**：

```
运行前: data/checkpoints.db 2026-10-14 17:11:44.676553200 +0800
        data/mm.db          2026-10-14 17:11:44.602366300 +0800
运行后: data/checkpoints.db 2026-10-14 17:11:44.676553200 +0800   ← 未变
        data/mm.db          2026-10-14 17:11:44.602366300 +0800   ← 未变
```

> 说明：`data/*.db` 的 17:11 时间戳来自**实现方**的 CLI 演示（其报告 §7 复现命令未传 `--ckpt/--db`，
> 落默认路径），与本轮测试无关。`artifacts/` 全程未被写入。用例文件内对 `data/` 的唯一引用是 docstring 说明文字。

---

## 4. ISSUES

### ISSUES-NFR5-01（中危，已用 xfail 固化）并发恢复：合规卡点可被非法裁定值静默跨过（fail-open）

- **位置**：`mmagent/agents/coordinator.py:455-467`（`resume()`）——
  `self._read_status(run_id)`（**456 行**）与 `self._graph.invoke(Command(resume=...))`（**467 行**）
  之间**没有锁、也没有把已校验的快照钉定**。
- **现象（真机复现：2 个并发 CLI 进程投同一挂起点，8 轮统计）**：

  ```
  SUMMARY (rcs, final_stage, 合规卡点被非法值跨过):
     ([0, 0], 'DONE', True)      ← 两个进程都「成功」，合规卡点被静默跨过
     ([0, 0], 'DONE', True)
     ([0, 2], 'ASSEMBLE', False) ← 受控拒绝（正确结果）
     ([0, 0], 'DONE', True)
     ([0, 0], 'DONE', True)
     ([0, 0], 'DONE', True)
     ([0, 0], 'DONE', True)
     ([2, 0], 'ASSEMBLE', False)
  ```

  8 轮中 **6 轮命中 fail-open**。终态 `decisions`（真实输出）：

  ```
  {"caliber_conflict":      {"resolution": "use_mapping", "note": "p0"},
   "pre_export_compliance": {"resolution": "use_mapping", "note": "p1"}}   ← 合规卡点拿到的是「口径形状」的裁定
  ```

  终态 `log` 出现 `gate:pre_export_compliance=None`（因为该 dict 没有 `decision` 键），
  即**合规卡点是在没有合法合规裁定的情况下被跨过、一路跑到 `DONE`**。
- **推测原因**：`resume()` 的「读状态（确认当前挂起点）→ invoke」是一段 TOCTOU 序列。
  两个进程都通过状态检查后，后到者的 `Command(resume=X)` 会被 langgraph 应用到
  **检查点里当前 pending 的那个 interrupt**（此时已推进到 `pre_export_compliance`），
  于是卡点 1 的裁定被卡点 3 消费。而 `_validate_decision()`（399-424 行）只校验**输入侧**
  （调用方声称的 gate），**不校验 interrupt 真正消费到的值**，故非法值畅通无阻。
- **附带症状（同一竞态的另一面）**：12 轮中出现 1 次子进程抛**未受控异常**并以 `rc=1` + 原始 traceback 退出
  （而非受控的 exit 2/3），使 CLI 退出码契约在并发下不成立：

  ```
  File "H:\mm-agent\mmagent\agents\coordinator.py", line 467, in resume
      self._graph.invoke(Command(resume=decision), config=self._config(run_id))
  File "H:\mm-agent\.venv\Lib\site-packages\langgraph\pregel\main.py", line 4001, in invoke
      for chunk in self.stream(
  ...
  sqlite3.OperationalError: attempt to write a readonly database
  ```
- **影响**：单机单人场景触发概率低（需两个进程在毫秒级窗口内同时恢复同一 run）；
  但一旦命中即「合规卡点被绕过 + 留痕与事实不符」，属本项目最忌讳的静默失效。
- **修复方向（未改源码，交队长决策）**：
  1. `invoke` 时在 config 里带 `checkpoint_id`，把已校验的那个快照钉住，让陈旧 resume 被 langgraph 自身拒绝；
  2. 或把「读状态 → 校验 → invoke」纳入同一写事务（`BEGIN IMMEDIATE`）串行化；
  3. 并在 invoke 之后兜底校验：若本节点消费到的裁定值不属该 gate 的取值域，则报错而非静默继续。
- **回归用例**：`TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点`
  （用 monkeypatch 把窗口**确定性**撑开，避免真机竞跑导致 flaky；`xfail(strict=False)`，
  **修复后请去掉 xfail 标记**）。真机 8 轮统计见上。

### ISSUES-NFR5-02（低危，观察项）`run status` 对不存在的 run_id 静默返回「未挂起」

- **位置**：`mmagent/agents/coordinator.py:357-378`（`_read_status`）、`mmagent/cli.py:269-279`（`cmd_run_status`）
- **现象（实测）**：

  ```
  $ python -m mmagent.cli run status --run-id never-any --ckpt zz.db --db zzl.db
  {"run_id": "never-any", "stage": "", "paused": false, "gate": null, "question": null, "done": false, "log": []}
  rc=0
  ```
- **推测原因**：`_read_status()` 把 langgraph 对未知 thread 返回的**空快照**直接映射成
  「`stage=""`、未挂起、未完成」，未区分「线程不存在」与「线程存在但未挂起」；
  且 `cmd_run_status` **未复用** `_require_ledger`（`start`/`resume` 都有前置校验，`status` 没有）。
- **影响**：打错 `--run-id` 时人看到的是「正常但没挂起」，属「安静地丢信息」类问题。
  `resume()` 路径不受影响（会明确抛 `InvalidGateDecision: 线程 ... 当前未挂起`）。
- **建议**：`values` 为空且 `snap.next` 为空时报告「未知 run_id」；`status` 与 `start/resume` 的前置校验口径对齐。
  **未写成用例**：当前行为未被任何契约冻结，避免把「待定行为」固化成断言。

### 观察项-3（备案，不列为缺陷）

- `record_success()` 在 `HALTED` 之后调用会写 `fail_counts['HALTED']=0`（实测 `{'SOLVE': 2, 'HALTED': 0}`），
  而 `HALTED` 计数从未被累加 —— task-001 遗留写法，非本轮回归，无功能影响。
- `hitl.gate_resumed` 留痕写在 `invoke` **之前**（`coordinator.py:465-467`）。若该次 invoke 期间进程被杀，
  台账会显示「已投递恢复」而检查点仍停在原卡点。语义上它记录的是「投递」而非「恢复完成」，本身自洽，
  但与 `hitl.gate_paused` 的「事实快照」语义不对称；建议后续在 README 里写明或对齐。
- brief 条目 E 在本轮**首次测试时已落地**（`cli.py:213-221`），因此未出现「先红后绿」的顺序 —— 如实记录。
- `_emit` 首次失败往 stderr 告警一次（`coordinator.py:330-355`，`_emit_degraded` 抑制刷屏）但**不抛异常**：
  与「观测失败不阻断流程」一致；本报告的 E 组用例是在 **CLI 前置校验**层面断言 exit 3，
  并未依赖 `_emit` 的告警通道（两者互补，不冲突）。

---

## 5. 未覆盖项（明确列出 + 原因）

| 未覆盖点 | 原因 |
|---|---|
| **真并发**下 resume 的「正确拒绝」行为 | 竞态窗口本身不确定（8 轮 6 命中 fail-open / 2 轮受控拒绝），直接以 2 进程竞跑写断言会 flaky。已改为**确定性** monkeypatch 用例（xfail）并附真机 8 轮统计作为证据（`ISSUES-NFR5-01`） |
| `get_state_history` 的**跨进程**读取 | 只在同进程验证了时间旅行；跨进程读历史与「读同一 SQLite 检查点库」等价，且 A1/A2 已证明跨进程可读。单独补测收益低 |
| 检查点库损坏 / 半写 WAL 的容错行为 | brief 未要求；属 NFR 错误处理边界，建议列入后续任务专项用例 |
| 真实 A 题数据全链路（intake → export 真实产物） | 本轮图节点是桩（只推进 `stage`/`log`，不产出 artifacts），不属 task-002 范围 |
| HTTP 层 HITL 路由（`app.py` / `api/*`） | 实现方明确本轮只做库接口 + CLI（其报告 §6.4），无被测对象 |
| `abort` / `fix_and_rerun` / `reject` 的**流转**语义 | 图是固定线性 10 节点（无回边），值只被**记录**不改变流转（实现方 §4.7 已声明为已知局限）。故只断言「值被合法记录」，不断言流转 |
| 高并发（>2 进程）与压力测试 | brief 未要求；本项目单机单人。与 `ISSUES-NFR5-01` 相关，建议一并设计 |
| 非 Windows 平台 | 仅在 Windows / Python 3.13.14 验证 |

---

## 6. 我的验证边界（不越界声明）

- 本轮**只新增/修改 `tests/test_nfr5_recovery.py`**，`mmagent/` 一字未改（发现缺陷全部走 §4 报告，未动手修）。
- 未改 `config/*.yaml`、未改 `.gitignore`、未碰 `data/mm.db` / `data/checkpoints.db`、未污染 `artifacts/`。
- 跨进程用例的检查点库与台账库一律用 `tmp_path` 隔离；子进程一律用 `sys.executable` 绝对路径 +
  `PROC_TIMEOUT = 180s` 超时保护；台账库在每个用例里**显式 `init_db`**，不依赖全局状态。
- 不做最终验证结论（由 Vic 独立验证）；本报告只给「是否有用例支撑」的判断与证据。

---

## 7. 复现命令

```bat
cd /d H:\mm-agent

:: 新增用例（含 xfail 明细）
.venv\Scripts\python.exe -m pytest tests\test_nfr5_recovery.py -q -rxX

:: task-001 基线（应 189 passed / 1 xfailed）
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests\test_nfr5_recovery.py

:: 全量
.venv\Scripts\python.exe -m pytest tests -q

:: 跨进程证据（真实 pid）
.venv\Scripts\python.exe -m pytest tests\test_nfr5_recovery.py::TestCrossProcessRecovery -q -s

:: 强制跑出并发竞态的真实失败输出（xfail 证据）——修复轮 2 已修复并摘除标记
.venv\Scripts\python.exe -m pytest tests\test_nfr5_recovery.py::TestConcurrentResumeRace -q
```

---

# 8. 修复轮 2 回归（Phase 3b，2026-10-14 19:00 追加）

本节为**追加**，前 7 节为第一轮定稿内容（未改动）。

## 8.1 本轮新增范围

- **新增文件**：`tests/test_hitl_decision_flow.py`（**32 用例**，md5 `74dab4501771597940bdd12a3cf5d087`）
- 被测修订：`mmagent/agents/coordinator.py` md5 `73184f979dff2c3ecd9e7ad32d92be62`（692 行）、
  `mmagent/cli.py` md5 `c155199992862b947e25f84cda4cc36a`
- `tests/test_nfr5_recovery.py` **本轮未改**（md5 `3888ada3fcabbd8bfa9d5acb1ae6de6a`；
  其并发竞态用例的 xfail 标记已由队长授权摘除，现为 PASS）
- `mmagent/` 一字未改（发现缺陷全部写入 §8.5，未动手修）
- **我的首轮用例被修正一处的说明（如实记录）**：`test_nfr5_recovery.py` 中并发竞态用例的断言
  原文为 `assert (decisions.get(GATE_COMPLIANCE) or {}).get("decision") in {"approve","reject"}`，
  该写法**对正确行为不可满足**（竞态修好后合规卡点保持"待审"，`decisions` 里根本没有
  `pre_export_compliance` 键，取值恒为 `None`）——这是我首轮写的断言缺陷。
  实现方按队长授权改为 `crossed is None or (crossed or {}).get("decision") in {"approve","reject"}`
  （"要么没被跨过，要么只被合法 approve/reject 跨过"），我复核认为**语义等同于本意、未被弱化**：
  修复前串台出的口径形状 dict 取值仍为 `None` → 断言仍会失败（证据见 §2.4 的 `--runxfail` 真实失败输出）。
  即该用例仍是"修复前红、修复后绿"的有效回归用例。

## 8.2 用例清单（文件 + 用例名 + 覆盖需求编号）

| 修复项 | 覆盖需求 | 用例（节点名） | 意图 |
|---|---|---|---|
| **F1 否定出口** | HITL 裁定语义 / FR-5 | `TestDecisionDrivenFlow::test_compliance_reject_终止于HALTED且不走到export` | reject → `stage=HALTED`、`halted=True`、`done=False`、`paused=False`；`log` **整表相等**且 `"stage:export" not in log` |
| **F1 否定出口** | HITL 裁定语义 | `::test_sanity_abort_终止于HALTED且不进入render与assemble` | abort → HALTED；`stage:render`/`stage:assemble`/`stage:export` 均不在 log |
| **F1 halt 留痕** | HITL 裁定语义 | `::test_halt写hitl_run_halted事件且不写run_finished` | `hitl.run_halted` 恰 1 条（含 gate/decision/stage）；`hitl.run_finished` 为 0 |
| **F1 正常出口** | FR-5 | `::test_肯定裁定仍正常到达DONE[sanity-accept / compliance-approve]` | 防止修否定出口把正常路径一起断掉 |
| **F1 正常出口** | FR-5 | `::test_三卡点全流程肯定裁定到达DONE` | 三卡点全肯定裁定 → DONE，`log` 整表相等 |
| **F1 caliber 路由** | FR-5 | `::test_caliber三种合法裁定均继续推进[use_mapping / keep_raw / human_override]` | 口径裁定只有「如何解决」语义 → 三种取值均应推进到卡点 3 |
| **F1 回边上限** | HITL 裁定语义 | `TestRerunBound::test_MAX_RERUN常量为1` | 阈值常量存在且为 1（防无限循环） |
| **F1 回边上限** | HITL 裁定语义 | `::test_fix_and_rerun首次重入solve后再次挂起` | 首次回边 → 回到 sanity 卡点再评审；`rerun:solve` 恰 1 次、`stage:solve` 恰 2 次 |
| **F1 回边上限** | HITL 裁定语义 | `::test_fix_and_rerun第二次超上限转HALTED` | 第二次 → HALTED，且不得再重入 solve，`stage:render` 不在 log |
| **F1 回边** | HITL 裁定语义 | `::test_回边后接受裁定可以正常走完` | 回边不是死路：重入后 accept 仍能到 DONE |
| **F2 payload** | HITL 裁定语义 | `TestReviewPayload::test_挂起payload恒含四键且review非None[卡点1/2/3]` | 原始 interrupt payload 键集 == `{gate,stage,question,review}`；无材料时 `review == {}` 而非 None |
| **F2 payload** | HITL 裁定语义 | `::test_有材料时review携带明细[口径材料/sanity材料/合规材料]` | 三类待审材料均原样透传到 `review` |
| **F2 payload** | HITL 裁定语义 | `::test_非挂起状态review为None` | 边界：DONE / HALTED 时 `review is None` |
| **F2 payload** | HITL 裁定语义 | `::test_CLI_start_conflicts时review能看到冲突明细` | `run start --conflicts` → 非空冲突明细 |
| **F2 payload** | HITL 裁定语义 | `::test_CLI_start_conflict_detail透传自定义材料` | `--conflict-detail` JSON 原样透传 |
| **F2 payload** | HITL 裁定语义 | `::test_CLI_start_conflict_detail非法JSON返回码2` | 非 JSON 材料 → exit 2（不静默） |
| **F4 退出码** | NFR-5 / FR-5 | `TestUnknownRunStatus::test_未知run_id退出码4并给出明确提示` | exit **4** + stderr 明确；不得输出看似正常的 JSON |
| **F4 退出码** | FR-5 | `::test_api层未知run_id抛UnknownRunError` | API 层抛 `UnknownRunError` |
| **F4 退出码** | FR-5 | `::test_已存在run的status退出码0` | 已挂起 run → exit 0 |
| **F4 退出码** | FR-5 | `::test_终止后run的status退出码0且halted为真` | halt 后 status → exit 0 且 `halted=True` |
| **F4 退出码** | FR-5 | `::test_台账未初始化时exit3优先于exit4` | 前置校验顺序：3（台账）先于 4（run_id） |
| **F3 受控错误** | NFR-5 | `TestCheckpointUnavailable::test_检查点库只读时CLI_start退出码3且无traceback` | 只读检查点库 → exit 3，stderr/stdout **均无 `Traceback`** |
| **F3 受控错误** | NFR-5 | `::test_检查点库只读时CLI_resume退出码3且无traceback` | 写路径不可用 → exit 3 + 明确提示 |
| **F3 受控错误** | NFR-5 | `::test_检查点路径不是数据库文件时退出码3` | `--ckpt` 指向目录 → exit 3，无原始 traceback |
| **F3 受控错误** | NFR-5 | `::test_检查点不可用时API抛CheckpointUnavailable` | API 层抛 `CheckpointUnavailable` |

**分类计数**：`TestDecisionDrivenFlow` 9 / `TestRerunBound` 4 / `TestReviewPayload` 10 /
`TestUnknownRunStatus` 5 / `TestCheckpointUnavailable` 4 = **32**。

## 8.3 运行结果（真实输出）

```
$ .venv\Scripts\python.exe -m pytest tests/test_hitl_decision_flow.py -q
................................                                         [100%]
32 passed in 93.18s (0:01:33)
```

```
$ .venv\Scripts\python.exe -m pytest tests -q
........................................................................ [ 56%]
...................x.................................................... [ 85%]
......................................                                   [100%]
253 passed, 1 xfailed in 373.55s (0:06:13)          ← 254 collected
```

```
$ .venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_nfr5_recovery.py --ignore=tests/test_hitl_decision_flow.py
...................x..........................                           [100%]
189 passed, 1 xfailed in 80.11s (0:01:20)
```

**计数口径（完全自洽）**：

| 口径 | passed | xfailed | failed | skipped |
|---|---|---|---|---|
| task-001 基线（排除两个新文件） | **189** | **1** | 0 | 0 |
| 第一轮 `test_nfr5_recovery.py` | 32 | 0 | 0 | 0 |
| 第二轮 `test_hitl_decision_flow.py` | **32** | 0 | 0 | 0 |
| 全量 `pytest tests -q` | **253** = 189+32+32 | **1** | **0** | **0** |

- 全量唯一 xfail 仍是 task-001 遗留的 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`；
- 第一轮那条并发缺陷 xfail **已修复**（F3 `checkpoint_id` 钉定 + tip 比对），标记已摘除 → 现为 PASS；
- **本轮新增 0 failed / 0 skipped**，task-001 的 190 例无退化。

> 操作提示（非缺陷）：本仓库跑全量时，pytest 结束阶段清理 `pytest-of-*/garbage-*`（>50 文件）
> 会被本机的批量删除保护拦下，**把 "N passed" 摘要行一起吞掉**。
> 建议加 `--basetemp=<显式目录>` 规避（本报告数字即用该方式取得），否则 `| tail` 看不到计数。

## 8.4 关键新行为的实测证据（真实输出片段）

**F1 否定出口（`log` 整表，可直观看到没有 `stage:export`）**：

```
reject:  stage='HALTED' paused=False done=False halted=True gate=None
         log=['stage:intake','gate:caliber_conflict=skip','stage:profile','stage:plan','stage:solve',
              'gate:sanity_blocking=skip','stage:render','stage:assemble',
              'gate:pre_export_compliance=reject','stage:halt']
abort:   stage='HALTED' paused=False done=False halted=True
         log=['stage:intake','gate:caliber_conflict=skip','stage:profile','stage:plan','stage:solve',
              'gate:sanity_blocking=abort','stage:halt']
rerun#1: stage='SOLVE' paused=True gate='sanity_blocking'
         log=[...,'gate:sanity_blocking=fix_and_rerun','rerun:solve','stage:solve']
rerun#2: stage='HALTED' halted=True
         log=[...,'rerun:solve','stage:solve','gate:sanity_blocking=fix_and_rerun','stage:halt']
```

**F1 halt 留痕**：

```
hitl.gate_resumed: {"run_id":"r-reject","gate":"pre_export_compliance","decision":{"decision":"reject",...}}
hitl.run_halted:   {"run_id":"r-reject","gate":"pre_export_compliance","decision":{"decision":"reject",...},"stage":"HALTED"}
（hitl.run_finished 无记录）
```

**F2 payload 四键**（三个卡点都实测）：

```
compliance payload = {'gate':'pre_export_compliance','stage':'ASSEMBLE',
                      'question':'导出前需人工确认 AI 使用披露与合规声明','review':{}}
keys = ['gate','question','review','stage']
review(有材料) = ['AI 使用声明','数据来源']      ← compliance
review(有材料) = [{'field':'评级'}]              ← caliber
```

**F4 / F3 退出码（独立子进程）**：

```
status(未知 run_id)       rc = 4 | stderr: [run status] 未知 run_id：'nope'（该线程没有任何检查点）
status(已存在/已挂起)     rc = 0
status(未知)+台账未初始化 rc = 3 | stderr: [run] 台账库 ... 缺少 events 表 ... db init --db ...
(a) --ckpt 指向目录       rc = 3 | Traceback in stderr: False | [run start] 无法打开检查点库 ...(unable to open database file)
(b) --ckpt 全新只读文件   rc = 3 | Traceback: False | [run start] 无法打开检查点库 ...(attempt to write a readonly database)
(b2) status 只读          rc = 3 | Traceback: False
(c2) resume 只读（WAL）   rc = 3 | Traceback: False | [run resume] 检查点库不可用（attempt to write a readonly database）
```

## 8.5 ISSUES

### ISSUES-HITL-01（低危，一致性/体验）`_DEFAULT_SANITY_REVIEW` 是死常量，两卡点「默认待审材料」不对称

- **位置**：`mmagent/cli.py:226-234`（`_DEFAULT_CONFLICTS_REVIEW` / `_DEFAULT_SANITY_REVIEW` 两个常量）、
  `mmagent/cli.py:253-269`（`cmd_run_start` 的 context 组装）
- **现象（实测）**：

  ```
  $ python -m mmagent.cli run start --run-id a --conflicts ...          # 无 --conflict-detail
  review = [{'field': '评级', 'reason': '同构外推列口径冲突（示例，见 README §9 已知数据问题 #3）', 'candidates': [...]}]

  $ python -m mmagent.cli run start --run-id b --sanity-blocking ...    # 无 --sanity-detail
  review = {}                                                          ← 空容器
  ```
- **推测原因**：`cmd_run_start` 为 `--conflicts` 写了 `else` 分支填 `_DEFAULT_CONFLICTS_REVIEW`（cli.py:261-262），
  但 `--sanity-blocking` 分支只处理 `args.sanity_detail`（cli.py:265-269），**没有 else**。
  于是 `_DEFAULT_SANITY_REVIEW`（cli.py:231-234）全仓库**零引用**（已用 grep 确认），成为死代码。
- **影响**：人工在卡点 2 打开 payload 时看到的 `review` 为空，与卡点 1 的体验不一致；
  不影响流程正确性，也不违反 F2 契约（无材料时**允许**为空容器）。属"该给的材料没给"。
- **建议**：补 `else: context["sanity_items"] = _DEFAULT_SANITY_REVIEW`，或删掉该死常量以免误导。
- **未写成 xfail 用例**：这是"默认示例材料"的一致性/体验问题，未被任何冻结契约约束；
  直接把它固化成断言会越界（会替实现方定义产品行为）。已在 §8.4 用实测输出留下证据。

### 观察项（备案，不列为缺陷）

- `RunStatus.review` 类型标注为 `dict | None`（coordinator.py:418），但**实际值可以是 list**
  （如 `review == [{'field': '评级'}]`、`['AI 使用声明']`）。建议放宽为 `list | dict | None`；
  当前不影响运行（Python 不强制），仅影响可读性与静态检查。
- 只读检查点库下 **`run status` exit 0 而 `run resume` exit 3**（读可成功、写不可用）。
  语义上合理（读操作不需要写权限），记录备查；若希望"库不可写时 status 也报错"，需队长决策。
- `_emit("hitl.gate_resumed")` 仍写在 `invoke` **之前**（coordinator.py:646-650，第一轮观察项-3 的结论不变）。
  本轮新增的 `tip_before`/`tip_after` 比对（coordinator.py:649-656）正好补上了"投递了但没生效"的检测，
  建议把该比对结论也写进台账（当前只在异常时才体现）。

## 8.6 本轮结论

- **F1（裁定驱动控制流）**：`reject`/`abort` 否定出口 → `HALTED` 且**不产出 export**；`approve`/`accept` 正常出口 → `DONE`；
  `fix_and_rerun` 回边上限 `MAX_RERUN=1`、超限转 halt —— **全部有回归用例支撑，32 条全绿**。
- **F2（review payload）**：三卡点 payload 恒含四键、无材料为空容器非 None、有材料原样透传 —— 已覆盖。
- **F3（受控错误）**：检查点库只读/非文件 → exit 3 + 明确提示 + **无原始 traceback** —— 已覆盖；
  第一轮的并发竞态缺陷用例现已转 PASS。
- **F4（退出码）**：未知 run_id → exit 4；台账未初始化 → exit 3（优先级正确）—— 已覆盖。
- **NFR-5 结论不变**：跨进程恢复**有测试用例支撑**（第一轮 §3 的 6 个不同 pid 证据 + 硬崩溃恢复）。
- 新增 1 条低危 ISSUES（默认材料不对称），**不阻塞**；无新增 failed/skipped。

**复现命令**：

```bat
cd /d H:\mm-agent

:: 修复轮 2 用例
.venv\Scripts\python.exe -m pytest tests\test_hitl_decision_flow.py -q

:: 全量（建议带 --basetemp，否则摘要行会被本机批量删除保护吞掉）
.venv\Scripts\python.exe -m pytest tests -q --basetemp="%TEMP%\mmagent-pytest-tess"

:: task-001 基线（应 189 passed / 1 xfailed）
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests\test_nfr5_recovery.py --ignore=tests\test_hitl_decision_flow.py
```


---

## 8. 补记（2026-10-14 修复轮 2 后，队长 Horizon 追加，均来自真实 pytest 输出）

> §1–§7 是**修复轮 2 之前**的快照（当时 220 passed / 2 xfailed）。修复轮 2（F1 裁定驱动控制流 /
> F2 review payload / F3 并发不串台 / F4 status exit 4）落地后，新增独立测试文件
> `tests/test_hitl_decision_flow.py`（32 例），并把并发缺陷用例摘除 xfail 转绿。

### 8.1 全量真实输出（队长复跑；用仓库内 basetemp 规避沙箱批量删除守卫导致的假 exit≠0）

```
$ .venv\Scripts\python.exe -m pytest tests -q -rxX -p no:cacheprovider --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp_lead"
........................................................................ [ 28%]
........................................................................ [ 56%]
...................x.................................................... [ 85%]
......................................                                   [100%]
253 passed, 1 xfailed in 195.12s (0:03:15)
PYTEST_EXIT=0
```

### 8.2 计数口径（自洽）

| 口径 | passed | xfailed |
|---|---|---|
| task-001 基线（原 7 文件） | 189 | 1 |
| `tests/test_nfr5_recovery.py`（task-002 首轮） | 32 | 0（原 1 条并发 xfail 已转绿） |
| `tests/test_hitl_decision_flow.py`（修复轮 2 新增） | 32 | 0 |
| 全量 `pytest tests` | **253** = 189+32+32 | **1** |

唯一 xfail 仍为 task-001 遗留 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`。

> 修复轮 2 已把 `TestConcurrentResumeRace::test_并发陈旧裁定不得串台到下一个卡点` 的 xfail 摘除并
> **PASS**（ISSUES-NFR5-01 在代码层修复：`resume()` 把挂起快照的 `checkpoint_id` 钉进 invoke config）。
> 该用例断言被实现方从「对正确行为恒不可满足」修正为符合其本意（合规卡点不得被**非法**裁定跨过），
> 已在 `implementer-report.md` §9.3 记录并交队长复核（队长已在 Phase 4 认可）。
