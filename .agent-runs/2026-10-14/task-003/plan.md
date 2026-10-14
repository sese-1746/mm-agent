# plan.md — task-003：FR-5 端到端可运行率（P0-2）

- 项目：mm-agent（场景一「数学建模竞赛协作 Agent」），全本地 Ollama + SQLite，零外网
- 仓库：`H:\mm-agent`｜解释器：`.venv\Scripts\python.exe`（Python 3.13.14）
- 前置：task-001（FR-2/5/6/9/13 + NFR-7）、task-002（人机回环）均已冻结交付
- 起点 HEAD：`cc181a1`（全量测试 253 passed / 1 xfailed）

---

## 1. 任务背景

FR-5「确定性执行」的**机制**在 task-001 已打通并被真实触发过四环：`generate → guard → run → repair → rerun`（见 `.agent-runs/2026-10-13/task-001/PROGRESS.md`、`artifacts/runs/smoke1..5`）。

但**端到端可运行率实测 0/3**：`qwen3.5:latest` 连续三次生成的脚本不可运行。已定位的两个根因：

1. **CJK 路径 quirk（稳定复现）**：模型把 `附件1 各门店客户满意度综合得分-A题.xls` 写成 `data/附件 1.xls`（在「附件」与「1」之间插了空格）→ `FileNotFoundError`。证据：`artifacts/runs/smoke5/solve.py` 第 13 行 `xlrd.open_workbook('data/附件 1.xls')`。
2. **xlrd 用法混淆**：`'Cell' object has no attribute 'strip'`（smoke2/3）。**并发现提示词本身写错了事实**：`mmagent/agents/prompts/coder.system.j2` 写「`sheet.row(i)` / `sheet.cell_value(r,c)` 返回的是 **Cell 对象**，取单元格值时必须写 `.value`」——实际上 `cell_value()` **直接返回值**、不返回 Cell。这条错误指令很可能就是模型困惑的来源。

另发现一处**文档承诺但未实现**的接口：`mmagent/tools/solve_tool.py` 的 docstring 写「regen_fn 见 `make_regen_fn`」，但全仓库 grep `make_regen_fn` **零实现**（只有这句 docstring）→ `regen_fn` 事实上没有官方适配器。

---

## 2. 目标（DoD）

1. **给出真实的可运行率基线**（不是估计值）：在同一任务规格下，分别测 **CJK 文件名** 与 **ASCII 文件名** 两种输入的处理结果，各 N 次，给出成功数与失败明细。**禁止沿用文档中 ≥70% 的假设值**。
2. 至少落地一条**产品级**改进，使端到端可运行率相对基线有**可测提升**，且改进是通用稳健性措施、**不是为过验收调 prompt**。
3. 补齐 `make_regen_fn`（把「重新生成」适配器做成正式接口，兑现 docstring 承诺），且**保持依赖方向**：`tools` 不 import `agents`。
4. **修正 coder 提示词中关于 xlrd 的事实性错误**，给出最小正确示例。
5. 全量测试不退化（保持 **253 passed / 1 xfailed**，唯一 xfail 为 task-001 遗留项）；task-002 已冻结语义不受影响。
6. 产出：`plan.md`、`baseline-repro.md`（真实失败明细）、`implementer-report.md`、`final-summary.md`（五项）。

---

## 3. 方法与判据

- **判据统一用 `runner.run_script` 的 `ok`**（= 未超时 且 `returncode == 0` 且 manifest 声明的产物都存在）。这是"可运行"的客观定义，不接受"看起来跑完了"。
- **失败分类**（每次记录）：`filename_quirk`（路径不存在/与输入名不符）、`xlrd_usage`、`other_python_error`、`manifest_missing`、`guard_violation`、`timeout`。
- 输入用**同一份真实数据**（`附件1 各门店客户满意度综合得分-A题.xls`）；CJK 模式保持原名，ASCII 模式在 workdir 内 staged 成 `data/att1.xls`。
- 改进项按**见效概率**排序推进：
  ① 输入文件 ASCII 化（staging）；② 修正提示词 xlrd 事实错误 + 最小示例；③ 修复轮回传上一版**完整源码**并约束「只输出 code」；④ 记录真实成功率。
- **工作区隔离**：探针产物一律落 `.agent-runs/2026-10-14/task-003/probe/`，不污染 `data/`、`artifacts/` 运行态目录。

---

## 4. 风险

| 风险 | 应对 |
|---|---|
| 模型输出不稳定，抽样次数少导致结论不可靠 | 明确记录 N 与逐次结果；结论给**可复现性描述**，不写成确定命中率 |
| 为过验收而调 prompt 粉饰 | 改进只用「事实修正 + 输入名稳健化 + 完整源码回传」三类通用手段；删改任何提示词都要说明理由，并保留基线对照 |
| 与 20:00 定时任务并行改同一批文件 | 同一时刻只允许一个执行体改 `mmagent/**`；另一侧只做只读复算 |
| 真实 Ollama 调用耗时（单次 30–180s） | 探针后台跑；单次生成失败不重试多于 1 轮 |

---

## 5. 范围

- **可改**：`mmagent/agents/coder.py`、`mmagent/agents/prompts/coder.system.j2`、`mmagent/pipeline/repair.py`、`mmagent/tools/solve_tool.py`、`mmagent/agents/base.py`（仅在必要时）
- **禁改**：`mmagent/store/schema.sql`、`mmagent/agents/coordinator.py`、`mmagent/cli.py` 既有语义、FR-2/FR-6/FR-9/NFR-7/FR-13 相关模块、`config/*.yaml`、`.gitignore`
- **不得回退**：`OllamaClient` 的 `think:false` + `num_predict` + thinking 兜底；`runner` 相对路径 `resolve()`；`guard` 的 import 白名单；`repair_once` 的 `regen_fn` 回调注入
