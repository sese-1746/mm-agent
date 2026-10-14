# brief-verifier.md — 验证员 Vic（task-002 Phase 4 独立验证）

- role_name: 验证员 Vic（独立于实现方 Max）
- 仓库：`H:\mm-agent`｜解释器：`.venv\Scripts\python.exe`（Python 3.13.14）
- 任务目录：`H:\mm-agent\.agent-runs\2026-10-14\task-002\`
- output_path: `H:\mm-agent\.agent-runs\2026-10-14\task-002\verification-report.md`
- 纪律：**只跑不改**。禁止修改 `mmagent/**`、`tests/**`、`config/**`、`README.md`。可写 `probe/` 与 `verification-report.md`。

## mission

对 task-002「人机回环（LangGraph interrupt + SqliteSaver）」做**独立对抗性验证**，产出
`verification-report.md`，结论必须是 **PASS / FAIL / BLOCKED** 三选一（禁止模糊表述）。

## 必须覆盖的验证项（每项留原始证据：命令 + 真实输出）

1. **真实「挂起 → 杀进程 → 重启 → 恢复」演示并推进到 DONE**
   - 用独立子进程跑真实 CLI：`run start --conflicts --sanity-blocking`（挂起在 `caliber_conflict`）；
   - 至少一次用 `os._exit(0)` 硬杀（不 close、不清理 WAL），新进程仅凭同一 `--ckpt` 续跑到 `DONE`；
   - **绕过应用层复核**：用原生 `sqlite3` 直接读检查点库，确认挂起态来自持久化（非内存），并断言到达 `DONE` 时 `log` 含 `stage:export`。
   - 一律用临时目录（`tmp_path`/probe 下）传 `--ckpt`/`--db`，**不得污染** `data/mm.db`、`data/checkpoints.db`。
2. **非法裁定被拦且不触发 langgraph `UnboundLocalError`**
   - 覆盖：`resume=None`、空 dict `{}`、投错 gate（挂 `sanity_blocking` 时投 `caliber_conflict`）；
   - API 层断言抛 `InvalidGateDecision`，**且** `type(exc).__name__ != "UnboundLocalError"` / `isinstance(exc, NameError) is False`；
   - 对抗性补充：直连 langgraph（绕过 `_validate_decision`）用 `Command(resume=None)` 复现 `UnboundLocalError`，**以证明该守卫确有存在必要**（此项只作对照证据，不算缺陷）。
   - CLI 层断言：非法裁定 `returncode == 2`，stderr 含明确原因。
3. **每个卡点 `hitl.gate_paused` 恰好 1 条**（同一 `(run_id, gate)`），多次 resume / status 不膨胀。
4. **冻结基线 8 文件 md5 逐个核对**：7 个源文件必须与 task-001 基线一字不差；`README.md` 属**已知有意变更**，当前应为 `361afccc9e0d0262604d5c4887ff454a`（在报告中标注并比对新旧值）。
   - 基线值见 `.agent-runs/2026-10-13/task-001/md5-baseline-final.md` 与 `PROGRESS.md`。
5. **`data/checkpoints.db` 未被 git 跟踪**（`git check-ignore -v` 命中 + `git ls-files data/` 为空）。
6. **`netguard-check` EXIT=0**（`.venv\Scripts\python.exe -m mmagent.cli netguard-check`）。
7. **并发恢复不串台（对抗性）**：两个独立进程同时恢复同一挂起点，**跨台命中 = 0**（合规卡点不得被非法裁定跨过）；只读检查点库场景必须**受控退出**（明确 exit code，**无原始 traceback**）。
8. **全量回归**：`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q -rxX -p no:cacheprovider --basetemp=".agent-runs/2026-10-14/task-002/probe/pytmp_vic"`
   - 期望 **253 passed / 1 xfailed / 0 failed**（唯一 xfail 是 task-001 遗留 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`）。
   - **注意**：不要用系统临时目录做 basetemp（会触发沙箱批量删除守卫导致假 exit≠0）；务必用仓库内 basetemp。

## success_criteria

- `verification-report.md` 落盘，含：结论（PASS/FAIL/BLOCKED）、逐项证据（命令 + 真实输出片段）、发现的缺陷清单（若有）。
- 不得为「过验收」调弱断言或编造输出；跑不通就如实记录失败输出与根因。
- 若发现缺陷：**不修**，只在报告中定位（文件:行 + 复现步骤）并给修复方向，交队长转实现方。

## 报告格式（建议）

```
# verification-report.md — 验证员 Vic
## 0. 结论（PASS/FAIL/BLOCKED + 一句话）
## 1. 验证环境（git HEAD、解释器版本、md5 快照）
## 2. 逐项验证（2.1 跨进程恢复 / 2.2 非法裁定 / 2.3 留痕 / 2.4 基线 / 2.5 git / 2.6 netguard / 2.7 并发）
## 3. 缺陷清单
## 4. 未覆盖项与原因
## 5. 复现命令
```

## 回传

仅回简短状态块：结论、关键计数、缺陷条数、报告路径。**不要倒全文。**
