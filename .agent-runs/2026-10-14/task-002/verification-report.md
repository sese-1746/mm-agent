# verification-report.md — 验证员 Vic（task-002 Phase 4 独立对抗性验证）

## 0. 结论

**PASS** — task-002「人机回环（LangGraph interrupt + SqliteSaver）」的 7 项必做验证 + 全量回归**全部通过**，未发现新缺陷。结论全部基于本机实跑的真实输出（命令与原文见 §2）。仅 1 条「观察/备注」（非缺陷，见 §3）。

- 全量回归：**253 passed / 1 xfailed / 0 failed**（215.42s），与期望**逐字相符**（唯一 xfail 为 task-001 遗留用例）。
- 真实「挂起 → `os._exit(0)` 硬杀 → 新进程重启 → 恢复」跑到 `DONE`，`log` 含 `stage:export`；原生 `sqlite3` 直读检查点库旁证挂起态来自持久化。
- 非法裁定（`None` / `{}` / 投错 gate）全部被 `InvalidGateDecision` 拦下，**未出现** `UnboundLocalError`；CLI 层 `returncode == 2`；对抗性对照**成功复现** `UnboundLocalError`（证明守卫必要，不算缺陷）。
- 每卡点 `hitl.gate_paused` 恰好 1 条；并发 10 轮跨台命中 = 0；只读检查点库受控退出（exit 3，无原始 traceback）。
- 冻结基线 7 源文件 md5 一字未变；`README.md` 为已知有意变更（现 `361afccc9e0d0262604d5c4887ff454a`）；`data/checkpoints.db` 未被 git 跟踪；`netguard-check` EXIT=0。

---

## 1. 验证环境

| 项 | 值 |
|---|---|
| 仓库根 | `H:\mm-agent` |
| git HEAD | `ca5fb64a9492017efe3b2f8a72d3f3017f7cbf48`（= `ca5fb64`，与任务书一致） |
| 解释器 | `H:\mm-agent\.venv\Scripts\python.exe` |
| Python | `3.13.14 (main, Jun 11 2026, 04:04:46) [MSC v.1944 64 bit (AMD64)]` |
| langgraph | `1.2.15` |
| langgraph-checkpoint-sqlite | `3.1.2` |
| langgraph-checkpoint | `4.3.0` |
| pytest | `9.1.1` |

**验证启动时 `git status`**（porcelain）：
```
?? .agent-runs/2026-10-14/task-002/brief-verifier.md
?? .agent-runs/2026-10-14/task-002/probe/lead_fullsuite.txt
?? .agent-runs/2026-10-14/task-002/probe/lead_fullsuite2.txt
?? .agent-runs/2026-10-14/task-002/probe/pytmp_lead/
?? tests/test_hitl_decision_flow.py
```
> 说明：验证过程中团队其它成员（team-lead）在 `.agent-runs/` 下并行产出复核材料，故 `git status` 后续新增若干 `.agent-runs/**` 修改/未跟踪项（`PROGRESS.md`、`test-report.md`、`lead_*` 等）。**均非本人所为**；本人未触碰 `mmagent/**`、`tests/**`、`config/**`、`README.md`（见 §2.4/§2.5）。

**本人只写以下路径**：`.agent-runs/2026-10-14/task-002/probe/vic/**`、`.agent-runs/2026-10-14/task-002/probe/vic_fullsuite.txt`、本报告。

---

## 2. 逐项验证（命令 + 真实输出）

### 2.1 跨进程「挂起 → `os._exit(0)` 硬杀 → 重启 → 恢复」→ DONE

**探针**：`probe/vic/vic_probe1_crossproc.py` + `probe/vic/vic_hardkill_resume.py`
**命令**：`.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe1_crossproc.py`

真实输出（节选，全部走真实 CLI 子进程 + 一次 `os._exit(0)` 硬杀）：
```
--- P1 start (挂起 caliber)  rc=0
    {"run_id":"vic-xp-1","stage":"INTAKE","paused":true,"gate":"caliber_conflict",...,"done":false,"log":["stage:intake"]}
--- P2 HARDKILL (os._exit(0) after resume caliber)  rc=0
    __PID__=32540
    __RESUME__={"stage":"SOLVE","paused":true,"gate":"sanity_blocking",...,"log":["stage:intake","gate:caliber_conflict=keep_raw","stage:profile","stage:plan","stage:solve"]}
--- P2b native sqlite3 直读检查点库（旁证）
    [sqlite] tables = ['checkpoints', 'writes']
    [sqlite] checkpoints: 7 rows
    [sqlite] writes: 33 rows
    [sqlite] 直读 thread_id='vic-xp-1' 的检查点行数 = 7
--- P3 status (新进程读盘)  rc=0
    {"stage":"SOLVE","paused":true,"gate":"sanity_blocking",...}
--- P4 resume sanity=accept  rc=0  -> paused at pre_export_compliance
--- P5 resume compliance=approve  rc=0
    {"stage":"DONE","paused":false,"done":true,"log":[...,"gate:pre_export_compliance=approve","stage:export"]}
--- P6 status (终态)  rc=0  -> done=true stage=DONE, log 含 'stage:export'
--- data/ 污染核对
    data\mm.db: OK(未变)  97bf454eacc052650eb06db9ee5444b9 -> 97bf454eac...44b9
    data\checkpoints.db: OK(未变)  1631fc948ab85872f7ec7e8927ba0e1b -> 1631fc948a...0e1b
```

**判定：PASS**。要点：
- 硬杀进程（`os._exit(0)`，不 close、不清理 WAL）在投 caliber 裁定后留下 `paused@sanity_blocking`；**全新进程**（P3）仅凭同一 `--ckpt` 读回该挂起态 → 证明挂起态来自**持久化**而非内存。
- 绕过应用层复核，用**原生 `sqlite3`** 直读检查点库：表 `checkpoints`/`writes` 存在，该 thread 7 条检查点行 → 旁证。
- 最终 `DONE`，`log` 含 `stage:export`。
- 全程用 `probe/vic/ws1/` 临时路径；`data/mm.db`、`data/checkpoints.db` md5 **未变**（零污染）。

### 2.2 非法裁定被拦且不触发 `UnboundLocalError`

**探针**：`probe/vic/vic_probe2_illegal.py`
**命令**：`.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe2_illegal.py`

真实输出（节选）：
```
--- resume(None): raisd  InvalidGateDecision: 非法人工裁定[caliber_conflict]：decision 不得为 None...
  [PASS] resume(None): 抛 InvalidGateDecision
  [PASS] resume(None): 类名 != UnboundLocalError（实际 InvalidGateDecision）
  [PASS] resume(None): not isinstance(exc, NameError)（实际 False）
--- resume({}):  InvalidGateDecision: ...decision 不得为空 dict...
  [PASS] ...（同上三项）
--- 投错 gate(挂 sanity 投 caliber):  InvalidGateDecision: ...投错卡点：当前挂起的是 'sanity_blocking'，收到 'caliber_conflict'
  [PASS] ...（同上三项）

【对抗性对照】绕过 _validate_decision 直连 langgraph Command(resume=None)
    raised: UnboundLocalError: cannot access local variable 'resume_is_map' where it is not associated with a value
    isinstance NameError = True
    => 复现成功：证明 _validate_decision 守卫必要（对照项，不算缺陷）

【CLI 层】
--- CLI resume=None(JSON null): rc=2  stderr=[run resume] 非法人工裁定[caliber_conflict]：decision 不得为 None...
--- CLI 空 dict {}:            rc=2  stderr=[run resume] 非法人工裁定[caliber_conflict]：decision 不得为空 dict...
--- CLI 投错 gate:             rc=2  stderr=[run resume] 非法人工裁定[pre_export_compliance]：投错卡点...
  [PASS] 三次均 returncode == 2、stderr 明确、无原始 traceback
  [PASS] 非法裁定后合法裁定仍可恢复（检查点未污染）
```

**判定：PASS**。API 层三类非法裁定均抛 `InvalidGateDecision`，`type(exc).__name__ != "UnboundLocalError"`、`isinstance(exc, NameError) is False`；CLI 层 `returncode == 2` 且 stderr 明确；**对抗性对照成功复现** `UnboundLocalError`（langgraph 1.2.15 `Command(resume=None)`），反证守卫确有必要。

### 2.3 每个卡点 `hitl.gate_paused` 恰好 1 条

**探针**：`probe/vic/vic_probe3_events.py`
**命令**：`.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe3_events.py`

真实输出（原生 sqlite3 直读台账库统计）：
```
【3a 单进程】 3 卡点走完 + 额外 3 次 status + 1 次失败 resume
  hitl.gate_paused per gate: {'caliber_conflict': 1, 'pre_export_compliance': 1, 'sanity_blocking': 1}
  [PASS] 每卡点 hitl.gate_paused == 1
  [PASS] gate_paused 总数 == 3（额外读/失败不膨胀）
  [PASS] gate_resumed == 3 / run_started == 1 / run_finished == 1

【3b 跨进程】 每步骤独立 CLI 子进程续跑
  跨进程 hitl.gate_paused per gate: {'caliber_conflict': 1, 'pre_export_compliance': 1, 'sanity_blocking': 1}
  [PASS] 跨进程每卡点 hitl.gate_paused == 1
  [PASS] 跨进程 gate_resumed == 3
```

**判定：PASS**。单进程与跨进程两种形态下，同一 `(run_id, gate)` 的 `hitl.gate_paused` 都恰好 1 条；额外 `status` 与失败 `resume` 均不使计数膨胀。

### 2.4 冻结基线 8 文件 md5

**命令**：`md5sum <8 files>`

| 文件 | 实测 md5 | 期望 | 判定 |
|---|---|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` | 同（task-001 基线） | ✅ |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` | 同 | ✅ |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` | 同 | ✅ |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` | 同 | ✅ |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` | 同 | ✅ |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` | 同 | ✅ |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` | 同 | ✅ |
| `README.md` | `361afccc9e0d0262604d5c4887ff454a` | **已知有意变更** `361afccc9e0d0262604d5c4887ff454a` | ✅ |

**判定：PASS**。7 个源文件与 task-001 基线（`.agent-runs/2026-10-13/task-001/md5-baseline-final.md` / `PROGRESS.md`）**一字不差**；`README.md` 由旧 `2175bb71faab5b4618373e8ca618fc92` → 新 `361afccc9e0d0262604d5c4887ff454a`，与任务书给定的**已知有意变更值完全一致**（文档更新，见 `README §7.5–7.9`）。

### 2.5 `data/checkpoints.db` 未被 git 跟踪

**命令与真实输出**：
```
$ git check-ignore -v data/checkpoints.db
.gitignore:20:data/*.db	data/checkpoints.db
$ git check-ignore -v data/mm.db
.gitignore:20:data/*.db	data/mm.db
$ git ls-files data/
(空)
$ grep -n data .gitignore
20:data/*.db
21:data/*.db-wal
22:data/*.db-shm
```
**判定：PASS**。命中 `.gitignore:20`，`git ls-files data/` 为空。

### 2.6 `netguard-check` EXIT=0

**命令**：`.venv\Scripts\python.exe -m mmagent.cli netguard-check`
**真实输出**：
```
[netguard-check] 通过：未发现 requests/socket/urllib/ftplib 直连
NETGUARD_EXIT=0
```
**判定：PASS**（验证前后各跑一次，均 EXIT=0）。

### 2.7 并发恢复不串台 + 只读受控退出（对抗性）

**探针**：`probe/vic/vic_probe7_concurrency.py`（10 轮，真实双进程）
**命令**：`.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe7_concurrency.py`

真实输出（节选）：
```
【7.1】10 轮并发恢复同一挂起点
  round 0: rc=(0, 0) 合规裁定=None 跨台=否 原始traceback=无
  ...
  round 6: rc=(2, 0) 合规裁定=None 跨台=否 原始traceback=无
  ...
  round 9: rc=(0, 0) 合规裁定=None 跨台=否 原始traceback=无
==> 10 轮跨台命中 = 0（期望 0）；含受控拒绝(rc=2)的轮 = 1；含原始 traceback 的轮 = 0（期望 0）

【7.2】只读检查点库 → 受控退出
  returncode = 3
  stderr = [run resume] 检查点库不可用（attempt to write a readonly database）；请检查 ...\ckpt.db 是否可写、是否被独占
  含原始 traceback? False
  => 受控退出判定: PASS
```
**判定：PASS**。10 轮对抗性并发下 `pre_export_compliance` **从未**被非合法（口径形状）裁定跨过（跨台命中 = 0）；1 轮出现**受控拒绝**（rc=2），符合「钉住已校验快照 → 陈旧裁定被拒」的设计；0 轮出现原始 traceback。只读检查点库 → **exit 3** + 明确提示 + 无原始 traceback。

### 2.8 全量回归

**命令**（仓库内 basetemp，避免系统临时目录批量删除守卫）：
```
cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q -rxX -p no:cacheprovider \
  --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp_vic"
```
**真实输出**（`probe/vic_fullsuite.txt`）：
```
........................................................................ [ 28%]
........................................................................ [ 56%]
...................x.................................................... [ 85%]
......................................                                   [100%]
=========================== short test summary info ===========================
XFAIL tests/test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕 - BEFORE INSERT 守卫是 SQL 触发器，RAISE(ABORT) 直接回滚语句，应用层无机会写 events 表 → 违规无审计留痕；需实现方在写路径捕获 IntegrityError 后补记事件（见 ISSUES-FR9-03）
253 passed, 1 xfailed in 215.42s (0:03:35)
EXIT=0
```
**判定：PASS**。**253 passed / 1 xfailed / 0 failed**，与期望**逐字相符**；唯一 xfail 为 task-001 遗留 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`。

> 备注：实现方 `implementer-report.md §9.7` 记录的收工全量为「221 passed / 1 xfailed」。差值 32 来自本轮新增的未跟踪测试文件 `tests/test_hitl_decision_flow.py`（测试工程师 Tess 新增），非回归差异。本次 253 为本任务书要求的期望值，实测一致。

### 2.9 补充验证：裁定驱动控制流 / halt 语义（P0 修复 F1）

**探针**：`probe/vic/vic_probe4_flow.py`（独立于测试文件）
```
① reject  -> HALTED halted=True done=False, log 无 'stage:export'         [PASS]
② abort   -> HALTED, log 无 'stage:render'/'stage:assemble'               [PASS]
③ fix_and_rerun#1 -> 回到 sanity 卡点（rerun:solve 1 次）；#2 超限 -> HALTED [PASS]
④ approve -> DONE, log 末项 'stage:export'                                [PASS]
```
**判定：PASS**（补充证据，佐证 P0 修复「裁定真正驱动控制流」成立）。

---

## 3. 缺陷清单

**新缺陷：0 条。**

**观察 / 备注（非缺陷，不影响 success_criteria）**：

1. **对照项成立**：langgraph 1.2.15 对 `Command(resume=None)` 确会抛 `UnboundLocalError`（§2.2 已复现）。`coordinator._validate_decision` 在调用 langgraph 之前拦截，是本设计的**必要守卫**——属**预期行为**，非缺陷。
2. **实现方报告格式**：`implementer-report.md` 的 `### 9.7 收工全量回归` 与 `### 9.8 已知局限` **重复出现两次**（同一内容，line 529–558 与 560–590）。仅文档冗余，不影响代码；建议实现方清理（不阻塞验收）。
3. **测试文件改动知情**：`tests/test_nfr5_recovery.py` 相比 task-001 附带的原版，被测试工程师按队长授权摘除 1 条 xfail 并**改写 1 条断言**（`test_并发陈旧裁定不得串台到下一个卡点`，见实现方报告 §9.3 的如实披露）。本人**独立**以真实双进程（§2.7，10 轮）复验底层行为：跨台命中 = 0，**改写后的断言与真实正确行为一致**，非「放宽掩盖缺陷」。此项为实现方已主动披露，转呈队长知悉。

---

## 4. 未覆盖项与原因

- **真机外网/网络隔离**：任务书要求「零外网」；本轮仅以 `netguard-check` 静态扫描（EXIT=0）与全程未触发任何网络调用作为证据，未做系统级断网注入（超出验证范围，且本项目无出网代码路径）。
- **同进程多线程共享同一 `Coordinator` 的细粒度并发**：实现方 §9.8 已列为已知局限（跨**进程**已实测 0 串台）。本轮未做同进程多线程压测（任务书验证项 7 明确为「两个独立进程」）。
- **HTTP 入口 HITL / 裁定回写业务库**：实现方 §9.8 列为 out of scope；本轮不做。

---

## 5. 复现命令

```bash
# 验证项1：跨进程 + 硬杀 + 原生 sqlite3 旁证
.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe1_crossproc.py
# 验证项2：非法裁定（API + CLI + 对抗性 UnboundLocalError 复现）
.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe2_illegal.py
# 验证项3：每卡点 hitl.gate_paused 恰好 1 条
.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe3_events.py
# 补充：裁定驱动控制流 / halt 语义
.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe4_flow.py
# 验证项7：并发不串台 + 只读受控退出
.venv\Scripts\python.exe .agent-runs/2026-10-14/task-002/probe/vic/vic_probe7_concurrency.py
# 验证项4/5：基线 md5 + git 跟踪
md5sum mmagent/pipeline/intake.py config/field_aliases.yaml mmagent/store/schema.sql \
       mmagent/pipeline/guard.py mmagent/store/db.py mmagent/checks/sanity.py \
       config/intake_scope.yaml README.md
git check-ignore -v data/checkpoints.db && git ls-files data/
# 验证项6：netguard
.venv\Scripts\python.exe -m mmagent.cli netguard-check
# 验证项8：全量回归（仓库内 basetemp）
.venv\Scripts\python.exe -m pytest tests -q -rxX -p no:cacheprovider \
  --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp_vic"
```

---

## 证据索引（相对 `H:\mm-agent`）

| 证据 | 路径 |
|---|---|
| 全量回归输出（253 passed / 1 xfailed） | `.agent-runs/2026-10-14/task-002/probe/vic_fullsuite.txt` |
| 验证项1 探针 + 硬杀脚本 | `.agent-runs/2026-10-14/task-002/probe/vic/vic_probe1_crossproc.py`、`vic_hardkill_resume.py` |
| 验证项2 探针 | `.agent-runs/2026-10-14/task-002/probe/vic/vic_probe2_illegal.py` |
| 验证项3 探针 | `.agent-runs/2026-10-14/task-002/probe/vic/vic_probe3_events.py` |
| 补充（控制流）探针 | `.agent-runs/2026-10-14/task-002/probe/vic/vic_probe4_flow.py` |
| 验证项7 探针 | `.agent-runs/2026-10-14/task-002/probe/vic/vic_probe7_concurrency.py` |
| 探针工作区（临时 ckpt/db 落盘物） | `.agent-runs/2026-10-14/task-002/probe/vic/ws1..ws7/` |
| pytest basetemp | `.agent-runs/2026-10-14/task-002/probe/pytmp_vic/` |
| task-001 冻结基线 | `.agent-runs/2026-10-13/task-001/md5-baseline-final.md` |

---

**最终结论：PASS**（7 项必做验证 + 全量回归全部通过；新缺陷 0 条；3 条观察/备注不阻塞验收）。
