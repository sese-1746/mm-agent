# final-summary.md — task-003 FR-5 端到端可运行率（P0-2）

- 任务：`.agent-runs/2026-10-14/task-003/`｜日期 2026-10-14｜仓库 `H:\mm-agent`（分支 `master`）
- 解释器 `.venv\Scripts\python.exe`（Python **3.13.14**）｜模型 `qwen3.5:latest`（Ollama 本地）

---

## FINAL STATUS

**PARTIAL（机制修复完成；可运行率未达标）**

- ✅ **基线与因果链已查清**：0/3 的根因**不是模型能力**，而是管道自身的 **7 条缺陷**（见 `baseline-repro.md §5.3`），其中 6 条已修、1 条部分修。
- ✅ **修掉一条使任务在结构上不可能完成的缺陷**：守卫白名单漏收 `xlrd`/`openpyxl`（详见下）。
- ✅ **判据不再撒谎**：`runner.ok` 原先对"零产物"报 `True`（假阳性），已收紧。
- ✅ 全量回归 **253 passed / 1 xfailed / 0 failed**（无回归）。
- ⚠️ **端到端可运行率仍低**：修复后最佳一次为 **ascii 1/3**（真实 PNG 203,595 字节）；cjk 仍 0/3。
  主要残留失败源是**模型产出畸形 JSON**（`Unterminated string`），其特征与定位见 KNOWN_ISSUES。

## CHANGED_FILES

| 文件 | 改动 | 是否在冻结基线内 |
|---|---|---|
| `mmagent/pipeline/guard.py` | 白名单补 `xlrd`/`openpyxl`；docstring 写明 `os` 为刻意整块禁止 | **是** → 已发布新基线 `00f172c1…`（PROGRESS.md §11） |
| `mmagent/pipeline/runner.py` | `ok` 增加 `manifest_present` 前置条件；报告新增 `manifest_present` | 否 |
| `mmagent/agents/base.py` | `json.loads(..., strict=False)` 容忍裸控制字符；失败时保留原始输出片段并带进 `LLMCallError` | 否 |
| `mmagent/agents/coder.py` | 补 `make_regen_fn`（兑现 solve_tool docstring 的承诺） | 否 |
| `mmagent/tools/solve_tool.py` | 新增 `stage_inputs`（输入 ASCII 化）与 `table_schema_digest`（FR-2→FR-5 schema 交接） | 否 |
| `mmagent/agents/prompts/coder.system.j2` | 修正 `cell_value` 事实错误；禁止吞异常；告知沙箱白名单；表头行规则 | 否 |
| `tests/test_fr5_guard_runner.py` | 2 处断言**加强**（零产物必须判失败；报告键集纳入 `manifest_present`） | 否 |

**未改**：`mmagent/store/*`、`pipeline/intake.py`、`checks/*`、`security/*`、`app.py`、`api/*`、`config/*.yaml`、FR-2/FR-6/FR-9/NFR-7/FR-13 语义。

## VERIFICATION

- `pytest tests -q`（显式 `--basetemp`）→ **253 passed, 1 xfailed in 557.51s**，0 failed / 0 skipped。
  唯一 xfail 为 task-001 遗留 `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`。
- FR-5 专项：`tests/test_fr5_guard_runner.py` → **43 passed**。
- 守卫行为回归（含安全性未被削弱）：
  `xlrd`/`openpyxl`/`pathlib`/`pandas+matplotlib` → **PASS**；
  `import os`、`from os import system`、`httpx`、`subprocess`、`importlib`、`eval` → **BLOCK**。
- 端到端实测：5 轮 × 2 模式 × 3 次，逐次记录产物；**R2 的数字因 `ok` 假阳性被判作废**（`baseline-repro.md §5.2`）。
- 新基线 md5 复算：8 项中 7 项未变，仅 `guard.py` = `00f172c113637251ea4bcf3e4269e4cd`。
- ⚠️ **本轮未做独立验证（无第三方成员复核实现方的成功声明）**；结论均来自交互会话自查 + 队长亲自复算，**不等同于 Phase 4 独立验证**。

## REPORTS

- 计划：`plan.md`｜基线复现与因果链：`baseline-repro.md`（含 §5 五轮实测矩阵与七条根因）
- 探针：`probe/att3_baseline.py`；原始记录：`probe/baseline/att3_baseline.json`；工作区：`probe/ws2_<mode>_<i>/`
- 冻结基线：`.agent-runs/2026-10-13/task-001/PROGRESS.md §11`

## KNOWN_ISSUES

| # | 级别 | 说明 | 建议 |
|---|---|---|---|
| 1 | **高** | **模型间歇性产出畸形 JSON**：`Unterminated string starting at line 6 column 11`，六次报错位置完全一致 → 断点在 `code` 值内部；同一提示词下时而全好时而全坏。已用 `strict=False` 容忍裸控制字符，但断裂点未定位 | 把**完整** raw 落盘（现仅留 500 字符）后定位；必要时改为「代码按行数组」的 schema 以规避 JSON 转义问题 |
| 2 | 中 | **FR-2 → FR-5 的 schema 交接未产品化**：`table_schema_digest` 目前由调用方手工调用 | 由流水线在 solve 前自动装配（读 `artifacts/intake/schema_report.json`） |
| 3 | 中 | 模型会**"自报错误"代替产出**：返回 `{"error": "文件不存在", ...}` 而非约定 schema（schema 校验正确拦下） | 提示词明确"不得返回 error 对象；无法完成也必须按 schema 输出" |
| 4 | 低 | **中文输入名仍被模型插空格**（`data/附件 1 …-A 题.xls`）——只是被 `stage_inputs` 绕过，根因未消除 | 保持 ASCII staging；无需再深入 |
| 5 | — | 采样量小（每格 3 次），所有成功率只作**可复现性描述**，不作为稳定命中率 | 需要更可靠的数字时把 N 提到 ≥10 |
| 6 | — | 本轮**无独立验证**（见 VERIFICATION 末条） | 续作时补 Phase 4 独立验证 |

**下一步建议**：① 先攻 KNOWN_ISSUES#1（当前最大失败源）；② 再做 #2 的流水线装配；③ 之后按 N≥10 重测给出可信可运行率。
