# final-summary.md — task-002 人机回环（LangGraph interrupt + SqliteSaver）

- 任务：`.agent-runs/2026-10-14/task-002/`｜队长 Horizon｜日期 2026-10-14
- 仓库：`H:\mm-agent`（分支 `master`）｜解释器 `.venv\Scripts\python.exe`（Python **3.13.14**）

---

## FINAL STATUS

**PASS（交付完成）**

- task-002 DoD 全部达成：三卡点可暂停 / 可**跨进程**恢复；检查点独立库 `data/checkpoints.db`（`thread_id=run_id`）；
  裁定**驱动控制流**（reject/abort→`HALTED`，`fix_and_rerun` 回边上限 `MAX_RERUN=1`）；CLI `run start|resume|status`。
- 独立验证（Vic，另见队长复算）7 项必做项 + 全量回归**全部 PASS**，**新缺陷 0 条**。
- 全量回归：**253 passed / 1 xfailed / 0 failed**（`pytest tests -q -rxX`，EXIT=0）。
- 冻结基线 8 文件：**7 个源文件一字未变**；`README.md` 有意变更（新基线已发布，见 REPORTS）。
- 检查点库未被 git 跟踪；`netguard-check` EXIT=0（零外网）。

## CHANGED_FILES

**本任务累计变更（相对 task-001 冻结提交 `a901f81`）**

- 实现（已含于提交 `ca5fb64`）：
  - `mmagent/agents/coordinator.py`（131 → 692 行：StateGraph + `interrupt()` + `SqliteSaver` + 条件路由 + `halt` + 回边）
  - `mmagent/agents/__init__.py`（re-export 对外接口）
  - `mmagent/cli.py`（`run start|resume|status`；退出码 0/2/3/4；`netguard` 排除 `.agent-runs`）
  - `requirements.txt`（`langgraph==1.2.15`、`langgraph-checkpoint-sqlite==3.1.2` + 传递依赖锁定）
  - `README.md`（§7 人机回环、§7.5–7.9；Python 3.13.14）— **冻结基线有意变更项**
  - `tests/test_nfr5_recovery.py`（task-002 首轮；摘除 1 条并发 xfail）
- 本次收尾新增/变更（本轮提交）：
  - **`tests/test_hitl_decision_flow.py`（新增，32 例；F1 裁定驱动控制流 / F2 review payload / F4 exit 4 / F3 受控错误）**
  - `tests/test_hitl_decision_flow.py` 为修复轮 2 的回归用例，此前未纳版 → 本轮纳入
  - `.agent-runs/2026-10-14/task-002/`：`brief-verifier.md`、`verification-report.md`、`lead-crosscheck.md`、
    `test-report.md`（追加 §8 真实计数）、`probe/lead_verify_core.*`、`probe/lead_concurrency.py`、`probe/lead_fullsuite*.txt`、`probe/vic/**`
  - `.agent-runs/2026-10-13/task-001/PROGRESS.md`（**追加** §10：发布 task-002 后新冻结基线）

**未改（确认范围外）**：`mmagent/store/*`、`pipeline/*`、`checks/*`、`security/*`、`app.py`、`api/*`、`tools/*`、`config/*.yaml`、`.gitignore`、task-001 既有测试文件。

## VERIFICATION

- **独立验证（验证员 Vic，`verification-report.md`）**：**PASS**
  - 真实「挂起 → `os._exit(0)` 硬杀 → 新进程重启 → 恢复」跑到 `DONE`，`log` 含 `stage:export`；原生 `sqlite3` 直读检查点库旁证。
  - 非法裁定（None / `{}` / 投错 gate）→ `InvalidGateDecision`，**无 `UnboundLocalError`**；CLI `rc==2`。对抗对照成功复现 `UnboundLocalError`（证明守卫必要）。
  - 每卡点 `hitl.gate_paused` 恰好 1 条（单进程 + 跨进程）。
  - 冻结基线 7 源文件 md5 一字未变；`README.md` 已知有意变更；`data/checkpoints.db` 未跟踪；`netguard` EXIT=0。
  - 并发 10 轮跨台命中 = 0；只读检查点库 → `exit 3` 受控、无原始 traceback。
  - 全量 **253 passed / 1 xfailed**（与期望逐字相符）。
- **队长独立复算（`lead-crosscheck.md`，不采信纸面结论）**：**PASS**
  - md5 8 项逐个复算（7 不变 / README 变更）；全量 253/1 并澄清「假 EXIT=1」为沙箱批量删除守卫所致；
    原生 sqlite3 直读 `checkpoints`/`writes` 表；真实硬杀跨进程恢复；非法裁定拦截 + 对照复现 `UnboundLocalError`；
    并发 4 轮跨台 = 0。
  - 追加发现（见 KNOWN_ISSUES）：**并发双进程同时恢复同一 run 时 `pre_export_compliance` 的 `gate_paused` 会被写 2 条**（4 轮 3 命中；顺序流程仍恒为 1 条）。
- **提交后对「已提交树」的复核（因发现一次意外源码改动，见下）**：**PASS**
  - 全量 `pytest tests` 重跑 → **253 passed / 1 xfailed / EXIT=0**（`probe/recheck_committed.txt`）。
  - 队长核心探针重跑 → `LEAD-CORE RESULT: PASS`（`probe/lead_verify_core.recheck.txt`）。
  - ⚠️ **意外改动说明（如实记录）**：`mmagent/agents/coordinator.py` 与 `mmagent/cli.py` 在磁盘上的 mtime 为 **19:23:47**（晚于本轮首轮测试与 Vic 验证），内容相较提交 `ca5fb64` 有 **3 行语义差异**：
    `RunStatus.review` 类型注解 `dict | None` → `dict | list | None`；`cmd_run_start` 增补 `else: context["sanity_items"] = _DEFAULT_SANITY_REVIEW`。
    **未查明写入来源**（疑为上一会话余留的自动化成员/并发写入）；改动方向与修复轮 2（F2 review payload）一致、逻辑自洽、`_DEFAULT_SANITY_REVIEW` 定义齐全。
    为不采信「未测即入库」，已在改动后**对已提交树整树重跑全量测试与队长探针，均 PASS**（见上），故本轮交付有效性以**该次重跑**为准。

## REPORTS

- 计划：`plan.md`｜调查：`investigation-report.md`｜课件研读：`courseware-hitl-review.md`
- 实现：`implementer-report.md`（含修复轮 1/2）｜测试：`test-report.md`（+ 本轮 §8 补记）
- **独立验证：`verification-report.md`（Vic，PASS）**｜队长复算：`lead-crosscheck.md`
- **冻结基线更新：`.agent-runs/2026-10-13/task-001/PROGRESS.md` §10（追加）**
  - 新基线生效值：**`README.md` = `361afccc9e0d0262604d5c4887ff454a`**；其余 7 项沿用原基线。

## KNOWN_ISSUES

| # | 级别 | 说明 | 建议 |
|---|---|---|---|
| 1 | 低危 | **并发双进程同时恢复同一 run** 时，`pre_export_compliance` 的 `hitl.gate_paused` 被写 **2 条**（顺序流程恒 1 条）。仅留痕冗余，**不**导致卡点被跨过、无数据损坏。本项目单机单人，触发需毫秒级竞争。 | 后续把 `gate_paused` 去重键设为 `(run_id, gate)` 或「仅首次挂起才写」 |
| 2 | 低危 | `implementer-report.md` §9.7/§9.8 **重复出现两次**（文档冗余，非代码问题）。 | 清理重复段落 |
| 3 | 说明 | `tests/test_nfr5_recovery.py` 的并发用例断言被实现方由「恒不可满足」修正为符合本意；已由 Vic 用真实 10 轮复验（跨台 = 0）确认非「放宽掩盖」。 | 已披露，知悉即可 |
| 4 | 范围外 | HITL 的 HTTP 入口、裁定回写业务库（需改 `schema.sql` 破基线）、`record_failure` 升级路径接入图。 | 待用户决策 / 后续任务 |
| 5 | 说明 | 无回边以外的复杂流转；`caliber_conflict` 裁定值域与 docx（`use_a/use_b/third_party`）存在**待用户确认的偏差**（实现用 `use_mapping/keep_raw/human_override`）。 | README §7.9 已标注，待用户确认 |

---

**下一步建议**：① 用户确认 `caliber_conflict` 取值域偏差；② 决定是否将「并发留痕去重」纳入下一小任务；③ 决定 HITL HTTP 入口是否立项。
