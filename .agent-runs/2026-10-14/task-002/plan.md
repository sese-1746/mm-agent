# plan.md — task-002 人机回环（LangGraph interrupt + SqliteSaver）

- 项目：场景一「数学建模竞赛协作 Agent（72 小时搭子）」，全本地、零外网
- 仓库：`H:\mm-agent`（2026-10-14 已 `git init`，初始提交 `a901f81`，tag `mm-freeze-2026-10-13`）
- 本轮日期目录：`.agent-runs/2026-10-14/task-002/`
- 前置：task-001 已冻结交付（`.agent-runs/2026-10-13/task-001/`），测试 190 例 = 189 passed / 1 xfailed

---

## 1. 任务背景

task-001 用**纯 Python 状态机**实现了 `mmagent/agents/coordinator.py`（131 行）：阶段表 `INTAKE → PROFILE → PLAN → SOLVE → RENDER → ASSEMBLE → EXPORT → DONE`，三个人工卡点（口径冲突 / sanity blocking / 导出前合规确认）目前是 `TODO(LangGraph)`，触发时抛 `HumanGateRequired` 并把 stage 置为 `HALTED`。

**缺陷**：`HALTED` 之后无法恢复。异常抛出即流程终止，状态只存在于内存里的 `CoordinatorState` 对象中，没有检查点持久化 —— 与文档主架构「**课件 7 号 Graph/Workflow：`StateGraph` + `interrupt()` + `SqliteSaver`**」冲突，也无法满足 NFR-5「丢失版本 ≤1」（进程崩溃/重启后必须能从最近检查点续跑，且最多丢 1 个版本）。

**用户裁定**（2026-10-14）：走**引入 langgraph** 路线，不自研。

---

## 2. 目标（DoD）

1. **三个卡点可暂停、可恢复**：卡点触发时流程挂起且检查点落盘；调用方给出人工确认后（同意/否决/改口径），流程从**挂起点**继续推进，不重跑已完成的阶段。
2. **跨进程恢复**：挂起后**进程退出**，新进程重新加载同一 thread 的检查点，能继续跑到结束。这是 NFR-5 的落地形式。
3. **检查点持久化到 SQLite**，且**优先使用独立检查点库**（如 `data/checkpoints.sqlite`）——见 §5 约束。
4. **接口可迁移**：`coordinator` 的对外调用点（`app.py` / `cli.py` / `tools/` / 测试）有明确迁移或兼容方案，不留"半边改完"的中间态。
5. **测试**：新增「暂停 → 重启 → 恢复」端到端用例；task-001 的 190 例保持全绿（除既有 1 条 xfail）。
6. **依赖与文档**：`requirements.txt` 补 langgraph 相关依赖并**锁定实际安装版本**；README 更新架构说明与命令。

---

## 3. 阶段与分工

| Phase | 成员 | 产出 |
|---|---|---|
| 0 分诊与建队 | 队长 Horizon | 本 `plan.md`、各 `brief-*.md` |
| 1 调查 | 调查员 Vera | `investigation-report.md`（含课件 demo 骨架、langgraph 版本可行性、现有调用点清单、NFR-5 原文） |
| 2 实现 | 实现工程师 Max | 改造 `coordinator.py`（或新增 graph 模块）+ 迁移调用点 → `implementer-report.md` |
| 3 测试 | 测试工程师 Tess | 新增跨进程恢复用例 → `test-report.md` |
| 4 验证 | 验证员 Vic | 独立跑全量 + 真实「挂起→杀进程→重启→恢复」演示 → `verification-report.md`（PASS/FAIL） |
| 5 审查 | 审查员 Ray（按需） | `review-report.md` |

---

## 4. 关键调查点（Phase 1 交给 Vera）

1. **课件骨架**：`C:\Users\Lenovo\Desktop\课件\agent\代码\10_LangGraph入门\` 下 `demo09_人机回环_程序完成人工的确认动作.py`、`demo10_人机回环_键盘输入.py`、`demo11_存储到SQLite.py`、`demo12_测试持久化存储.py` 与 `checkpoints.sqlite`；以及 `1012_Rag-Agent-mysql-部署&LangGraph入门` 目录。提取：`StateGraph` 定义、`interrupt()` 用法与恢复方式、`SqliteSaver` 的构造与 `thread_id` 约定。
2. **langgraph 可行性**：本机 Python 3.13.12，`langgraph` / `langgraph-checkpoint-sqlite` 是否有 cp313 wheel、当前最新版本号、`interrupt()` 在该版本的**语义**（较新版本要求 `Command(resume=...)`，老版本用 `NodeInterrupt`；必须给出实测证据，不能凭记忆）。
3. **现有调用点清单**：全仓库 grep `coordinator` / `HumanGateRequired` / `CoordinatorState` / `Stage`，列出每一个 import 点与用途（`app.py`/`cli.py`/`tools/`/`tests/`）。
4. **NFR-5 原文**：从 `docx_dump.txt` 取出「丢失版本 ≤1」与「人机回环/检查点」相关原文，作为验收依据。
5. **task-001 冻结基线**：确认 8 个 md5 文件里哪些会被本轮改动波及（尤其 `README.md` 在基线内）。

---

## 5. 约束（必须遵守）

- **保护 task-001 冻结基线**：`mmagent/store/schema.sql` 在基线内。**优先不修改它** —— langgraph 的检查点表应落在**独立 sqlite 文件**（如 `data/checkpoints.sqlite`），避免污染 `data/mm.db` 的 `profile_doc` 不可变保证与触发器语义。若调查后确认必须改 `schema.sql`，Max 须在报告中显式说明理由，并由队长决定是否重新发布基线。
- **不回归已验收语义**：FR-2 体检、FR-5 guard/runner、FR-9 档案版本链、NFR-7 脱敏的行为不得改变。
- **零外网**：不得引入任何需要联网运行的依赖；langgraph 相关包须本地 pip 安装即可跑。
- **依赖安装隔离**：所有包只装进 `H:\mm-agent\.venv`，不用全局 pip。
- **失败不粉饰**：跑不通就如实记录失败输出与根因，禁止为过验收而调弱断言或伪造成功。
- **改源码须通知测试**：任何触碰 Tess 已测模块的改动，必须先通知 Tess 重跑。

---

## 6. 风险

| 风险 | 应对 |
|---|---|
| langgraph 在 Python 3.13 上无 wheel / API 与课件版本不一致 | Phase 1 先验证可行性并给出实测版本与 API 证据；不可行则上报队长重新决策 |
| `interrupt()` 语义随版本变化，照抄课件可能跑不通 | Vera 必须实测跑一遍课件 demo，给出真实输出 |
| 改造打破 task-001 的 190 例测试 | Phase 3 全量回归；Max 改动前先跑一次基线确认起点绿 |
| 检查点与现有 `events` 留痕表职责重叠 | 明确分工：`events` 记业务事件，检查点记 Graph 状态；报告中写清 |
| README 在冻结基线内，改动会使基线失效 | 接受，但需重新发布基线并在报告中标注 |

---

## 7. 交付物清单（本轮结束时）

- `.agent-runs/2026-10-14/task-002/` 下：`plan.md`、`brief-investigator.md`、`brief-implementer.md`、`brief-test-writer.md`、`brief-verifier.md`、`investigation-report.md`、`implementer-report.md`、`test-report.md`、`verification-report.md`、`final-summary.md`
- 代码：`mmagent/agents/` 下的人机回环实现 + 调用点迁移 + 新测试
- 一次 git 提交，提交信息引用本任务目录
