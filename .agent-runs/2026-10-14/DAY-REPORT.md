# DAY-REPORT.md — 2026-10-14 工作日报（mm-agent）

- 项目：场景一「数学建模竞赛协作 Agent（72 小时搭子）」，全本地 Ollama + SQLite，零外网
- 仓库：`H:\mm-agent`（分支 `master`）｜解释器 `H:\mm-agent\.venv\Scripts\python.exe`（**Python 3.13.14**）
- 远端：Gitee 私有大仓库 `https://gitee.com/sese_1_0/mm-agent`（本日新接入）
- 今日任务目录：`.agent-runs/2026-10-14/task-002/`（人机回环）、`.agent-runs/2026-10-14/task-003/`（FR-5 端到端）

---

## 0. 今日概览

| # | 事项 | 结果 |
|---|---|---|
| 1 | **接入版本控制**（Gitee 私有仓库 + 凭据隔离 + 防 CRLF 化） | ✅ 完成 |
| 2 | **task-002 人机回环**（LangGraph `interrupt()` + `SqliteSaver`） | ✅ **已交付、已独立验证、已推送** |
| 3 | **task-003 FR-5 端到端可运行率**（P0-2） | ⚠️ **部分完成**：7 条根因中 6 条已修、1 条修正了诊断方向；**可运行率仍低** |

**提交链（全部已推送，远端 `master` = 本地 HEAD）**

```
e28cb3e  fix(fr5): 修掉「任务在结构上无法通过守卫」等 7 条管道缺陷（task-003）
cc181a1  docs(task-002): 校准 KNOWN_ISSUES#1 的可复现性描述
6534363  docs(task-002): 补实现方 F6 报告；队长复核记录
e4a2e22  docs(task-002): 提交后整树复跑（253 passed / 1 xfailed）并记录一次意外源码改动的重验证
d648678  test(hitl): 纳入裁定驱动控制流回归并完成 task-002 Phase 4 独立验证
ca5fb64  feat(hitl): 人机回环落地 LangGraph interrupt + SqliteSaver，裁定驱动控制流
a901f81  chore: 初始化仓库并冻结 task-001 交付
```

---

## 1. 接入版本控制

- 用户提供的令牌经实测是 **Gitee 私人令牌**（32 位十六进制），**不是** GitHub（GitHub 返回 401）。账号 `sese_1_0`。
- 新建私有仓库并完成首次推送；**令牌经 Windows 凭据管理器保存**，**未写入 `.git/config`**（grep 验证 0 命中明文）。
- **`.gitattributes` 强制 `eol=lf`**：本机 `core.autocrlf=true`，若不干预，检出时会把工作区 CRLF 化 → task-001 的 8 个冻结 md5 会当场失效。
- `.gitignore` 取舍：忽略 `.venv/`、`data/*.db*`、`artifacts/runs|tmp/`、`.workbuddy/`、探针产出的 PDF 副本与库文件；**保留** `artifacts/intake/`（冻结产物）、`artifacts/reports/`、`.agent-runs/`（流程交付物）。

---

## 2. task-002 人机回环（已完成）

### 2.1 目标与实现

把 `coordinator.py` 从「纯 Python 状态机 + 抛异常挂起（**暂停后无法恢复**）」改造为 **LangGraph `StateGraph` + `interrupt()` + `SqliteSaver`**：

- 检查点独立库 `data/checkpoints.db`，`thread_id = run_id`（**不碰** `schema.sql`，避免污染 `profile_doc` 不可变语义）
- 固定 10 节点线性图：`intake → gate_caliber → profile → plan → solve → gate_sanity → render → assemble → gate_compliance → export`
- 三卡点裁定契约：`caliber_conflict` / `sanity_blocking` / `pre_export_compliance`
- CLI 新增 `run start|resume|status`；退出码 **0=挂起正常 / 2=裁定非法 / 3=台账未初始化 / 4=run_id 不存在**

### 2.2 过程中揪出的三个真缺陷

| # | 缺陷 | 后果 |
|---|---|---|
| 1 | **裁定只被"记录"、不被"消费"**：卡点节点无论 approve/reject 都置 `confirmed=True`，整图无 `add_conditional_edges` | **审计显示"已否决"，产物却照导出**（比没有卡点更危险）；违反需求 docx:443「导出前校验缺失即阻断」 |
| 2 | **并发 TOCTOU → 合规卡点 fail-open**：`resume()` 的「读状态→invoke」未钉快照，两进程同时恢复时，后到者的裁定被应用到**下一个**卡点 | 8 轮 **6 轮命中**：合规卡点被非法裁定静默跨过并跑到 DONE |
| 3 | **台账库未初始化时事件静默全丢**：`_emit` 用 `except Exception: pass` 全吞，CLI 从不初始化台账 | 完整流程 exit 0、**0 条事件、零提示** |

### 2.3 修复与验证

- 三处修复：**条件边 + `halt` 终态 `HALTED`**（`fix_and_rerun` 回边上限 `MAX_RERUN=1`）、**`resume` 钉住 `checkpoint_id`** + `sqlite3.OperationalError` 受控退出、`_emit` 降级告警一次 + 台账缺失 **exit 3**。
- **独立验证（验证员 Vic，`verification-report.md`）**：**PASS** —— 真实 `os._exit(0)` 硬杀后新进程恢复跑到 `DONE`、**原生 sqlite3 直读检查点库**旁证、非法裁定三类全拦且**成功复现 `UnboundLocalError` 作为对抗对照**、并发 10 轮跨台 0、每卡点 `hitl.gate_paused` 恰好 1 条。
- 测试：**253 passed / 1 xfailed / 0 failed**（189 task-001 基线 + 32 + 32 新增；唯一 xfail 为 task-001 遗留审计留痕项）。

### 2.4 关键协作事实

- **19:00 的定时任务独立完成了 Phase 4**：自行建 `brief-verifier.md`、起独立验证员、产出 `verification-report.md` 与 `final-summary.md`；**发现我在并行改同一批文件后，对整树重跑复测才认账**（处理正确）。
- **代价**：与我的交互会话发生了一次**写冲突**（我派 F6 时它正在验证）。处置：把定时任务 prompt 改为「**先读盘核实、从断点续跑**，若发现另一执行体正在改同一批文件则只做独立验证」。

---

## 3. task-003 FR-5 端到端可运行率（部分完成）

### 3.1 结论先行（诚实版）

**可运行率仍然很低（最好的一次 ascii 1/3），但失败原因被整个换掉了**：从「任务在结构上过不了守卫」变成「生成被截断 + 少数语义误判」。
**七条根因里没有一条是"模型不够聪明"，全部是管道自身问题。**

### 3.2 七条根因与处置

| # | 层 | 问题 | 处置 |
|---|---|---|---|
| 1 | **守卫** | 白名单漏收 `xlrd`/`openpyxl`，而任务就是读 `附件1~6(.xls)`、提示词还教用 xlrd → **结构上过不了守卫** | 白名单补入两者；`os` 保留整块禁令（测试明确断言） |
| 2 | **判据** | `runner.ok` 对「零产物」报 `True`（`manifest_missing` 只在 manifest 存在时才统计） → **假阳性** | `ok` 增加 `manifest_present` 前置 + 报告字段 |
| 3 | **提示词** | 称 `cell_value()` 返回 Cell 对象（**事实错误**） | 改为最小正确示例 |
| 4 | **提示词** | 未禁吞异常 → 失败伪装成 `rc=0` | 明确"失败必须非 0 退出"；实测生效（失败脚本改回 `raise`） |
| 5 | **命名** | 模型稳定给中文名插空格（`data/附件 1 …-A 题.xls`） | 新增 `solve_tool.stage_inputs()`（ASCII 化） |
| 6 | **模块交接** | **FR-2 算出的真实 schema 从未传给 FR-5**（附件1 列名就是 `1.0`…`12.0`、表头在第 1 行） | 新增 `solve_tool.table_schema_digest()` |
| 7 | **生成截断** | 生成预算 **8192 > 运行态 ctx 4096** → 输出在 JSON 中途被硬截断（`Unterminated string`） | 新增 `ollama.num_ctx: 16384` 并显式下发；**修复尚未端到端验证** |

### 3.3 五轮实测（每轮 3 次 × 2 模式，真跑 `qwen3.5:latest`）

| 轮次 | 配置 | cjk | ascii |
|---|---|---|---|
| R1 | 基线 | 0/3 | 0/3 |
| R2 | 仅放行 xlrd | 3/3* | 2/3*  ← **数字作废（`ok` 假阳性，产物目录空无一物）** |
| R3 | 收紧 `ok` + 禁吞异常 | 0/3 | **1/3**（真成功：PNG **203,595 字节**） |
| R4 | + schema 交接 | 0/3 | 0/3 |
| R5 | + `strict=False` | 0/3 | 0/3 |

> 采样量小（每格 3 次），**只作可复现性描述，不作为稳定命中率**。

### 3.4 今日最重要的一次更正（协作纠错）

我原判第 7 条为「模型把 `\n` 写成真实换行 → `strict=False` 可治」。**定时任务的交叉复核反驳了我**，我复核后确认**它对、我错**：

```
A 字符串未闭合(截断)   strict=True  → Unterminated string
A 字符串未闭合(截断)   strict=False → Unterminated string   ← 治不了
B 裸换行但字符串闭合    strict=True  → Invalid control character
B 裸换行但字符串闭合    strict=False → OK                    ← 只治这种
```

线上报错是 **A 类（截断）**，`strict=False` **不对症**。真正的机制是 **`num_predict`(8192) > Ollama 运行态 `ctx`(4096)**（`/api/ps` 实测两模型均为 4096，而模型本体上限 262144），且 **`OllamaClient.chat` 从不发送 `num_ctx`**。已改为显式下发 `num_ctx: 16384`。

---

## 4. 冻结基线（今日两次变更）

| 版本 | 变更项 | 生效值 |
|---|---|---|
| task-001 原基线（§1） | 8 项 | 见 `PROGRESS.md §1` |
| task-002 后（§10） | `README.md` | `361afccc9e0d0262604d5c4887ff454a` |
| **task-003 后（§11）** | **`mmagent/pipeline/guard.py`** | **`00f172c113637251ea4bcf3e4269e4cd`** |

其余 6 个源文件（`schema.sql`/`db.py`/`intake.py`/`sanity.py`/两个 yaml）**本日全程一字未变**。

---

## 5. 今日的翻车与更正（如实记录）

| # | 事项 | 性质 |
|---|---|---|
| 1 | **误报"改进成果"**：据 `ok=True` 报出「0/3 → 5/6」，翻产物目录才发现**空无一物** | 我采信了会撒谎的布尔标志。**改进数字必须落到真实产物上核对** |
| 2 | **归因错误**：把 0/3 归因为"模型输出质量" | 实为守卫把任务必需的库禁掉；后被定时任务再次纠正第 7 条诊断 |
| 3 | **指令表意不清**：一句"不追了"被理解为"回退"，造成 aliases/README/产物三方短暂不一致 | 消息往复 3 轮才收敛 |
| 4 | **覆盖文件**：用 Write 覆盖 `baseline-repro.md`，丢掉已写的 §1–§4 | **追加要用 Edit，不要用 Write** |
| 5 | **看守代码自身出错**：判"是否执行 export"用了子串匹配，被 `gate:pre_export_compliance=reject` 命中误判 | 应使用精确成员判断 |
| 6 | **长时间并发作业**：交互会话 + 定时任务 + 验证成员三方同时改同一批文件 | 观测到高负载下 `199 errors`，负载退去后 `253 passed`；**同一任务目录不要让两个执行体并行** |

---

## 6. 未完成 / 下一步

| 优先级 | 事项 | 说明 |
|---|---|---|
| **P0** | **验证 `num_ctx: 16384` 是否真解决截断** | 修复已落地但**未端到端验证**（独占实测被中断；Vic 的端到端跑在修复前）。须在**独占环境**重跑 task-003 探针 |
| P1 | 把 **FR-2 → FR-5 的 schema 交接产品化** | 目前由调用方手工调 `table_schema_digest`，应由流水线在 solve 前自动装配 |
| P1 | 收短修复轮输入 | 回传完整 `prev_code` 会挤压 prompt（并行复核实测 17432 字符 prompt 被 Ollama 静默截断到 2050 token） |
| P2 | 提示词禁止"自报错误对象" | 模型曾返回 `{"error": "文件不存在", ...}` 代替 schema 输出 |
| P2 | 以 **N≥10** 重测给出可信可运行率 | 当前每格 3 次，只够作趋势判断 |
| — | 补 Phase 4 独立验证 | task-003 **无独立验证结论**（Vic 只做了部分项） |

**仍待用户（何昆阳）确认的历史项**：① 文档 spotcheck 示例 `214`/`63`/`7` 的出处（已穷举证伪）；② 附件3 的 17 处文本 `97.3.4` 的正确值；③ `caliber_conflict` 裁定值域与 docx 的偏差（实现用 `use_mapping/keep_raw/human_override`）。

---

## 7. 收工时的环境与仓库状态

- 工作区：`config/models.yaml`、`mmagent/agents/base.py` 有未提交改动（`num_ctx` 修复）；其余为 task-003 探针产物。
- 测试：全量 **253 passed / 1 xfailed / 0 failed**（在负载退去的独占窗口实测）。
- 无并发执行体（21:12 实测无其他 python 进程）。
- 三个团队成员（Vera/Max/Tess）均为已完成/暂停态。
