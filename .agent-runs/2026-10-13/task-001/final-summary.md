# final-summary.md — 续作交接说明（task-001 收尾）

> 配套阅读：同目录 `PROGRESS.md`（进度快照 + 冻结 md5 + 已知偏差/数据问题）。
> 项目：场景一「数学建模竞赛协作 Agent（72 小时搭子）」，全本地、零云支出。

---

## 0. 续作第一步（照做即可）

```bash
cd H:\mm-agent

# ① 先确认冻结基线没被动过（8 个 md5，见 PROGRESS.md §1）
md5sum mmagent/pipeline/intake.py config/field_aliases.yaml README.md \
       mmagent/store/schema.sql mmagent/pipeline/guard.py \
       mmagent/store/db.py mmagent/checks/sanity.py config/intake_scope.yaml

# ② 跑一遍全量测试，确认起点是绿的
.venv\Scripts\python.exe -m pytest tests -q

# ③ 建议：git init 并提交当前状态，打 tag 作为冻结点（当前仓库无 .git）
git init && git add -A && git commit -m "mm-agent freeze 2026-10-13 (task-001)"
git tag mm-freeze-2026-10-13
```

然后按 `PROGRESS.md §4` 的优先级开工：**P0-1 人机回环（LangGraph）→ P0-2 FR-5 端到端 → P0-3 golden 冻结 → P0-4 FR-9 字段溯源**。

---

## 1. 环境与命令速查

| 项 | 值 |
|---|---|
| 解释器 | `H:\mm-agent\.venv\Scripts\python.exe`（Python 3.13.12） |
| 已装依赖 | fastapi / uvicorn / pandas / xlrd / openpyxl / matplotlib / python-docx / pydantic / pyyaml / httpx / jinja2 / pytest（见 `requirements.txt`） |
| Ollama | `http://localhost:11434`；生成模型 `qwen3.5:latest`、降级 `qwen3.5:0.8b`、embedding `qwen3-embedding:latest` |
| 数据根（只读） | `C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题` |
| 数据库 | `data/mm.db` |
| 冻结产物 | `artifacts/intake/`（8 表 / 153 字段 / 5 冲突） |

```bash
# 建库（幂等）
.venv\Scripts\python.exe -m mmagent.cli db init --db data\mm.db
# 附件体检（FR-2）
.venv\Scripts\python.exe -m mmagent.cli intake --dataset "C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题" --out artifacts\intake
# 环境自证（NFR-10 雏形）
.venv\Scripts\python.exe -m mmagent.cli env-attest --out artifacts\reports\env_attest.json
# 零外网静态检查（AST）
.venv\Scripts\python.exe -m mmagent.cli netguard-check
# 脱敏自检（NFR-7）
.venv\Scripts\python.exe -m mmagent.cli desensitize-check --input <file>
# 起服务
.venv\Scripts\python.exe -m uvicorn mmagent.app:create_app --factory --port 8000
# 全量测试
.venv\Scripts\python.exe -m pytest tests -q
```

API（`app.py`）：`GET /api/v1/health`、`POST /api/v1/intake/run`、`POST /api/v1/solve/run`、`GET /api/v1/profile/{id}`、`POST /api/v1/compliance/disclosure`；统一错误体 `{error_code, message, detail, trace_id}`。

---

## 2. 每个模块怎么自测（不跑 pytest 也能验）

```python
# FR-9 版本链
from mmagent.store import db, profile
# write_version / adopt_new_version / read_profile('last') → 应带 status 字段
# initialized 库上试 INSERT OR REPLACE 打 adopted 行 → 必须 ABORT 'adopted immutable'

# FR-2 体检：跑 CLI intake 后看 artifacts/intake/
#   关键断言：附件1=13 / 附件2=25 / 附件3=25 / 附件4=13 / 附件5=13 / 附件6=37 字段
#   附件6 numeric_sum 合计 = 58633.0（定额 27289 + 实际 31344）
#   附件2 「12月.评级」value_counts = {合格:144,不合格:13}（注意键名是 field_name，不是 field_raw）
#   全产物中"missing_rate>0.99 且未标 all_blank_as_zero" 的列应为 0 条

# FR-5 守卫与执行
from mmagent.pipeline.guard import validate_script_ast, ScriptGuardError
# 拦：from os import system / importlib.import_module("subprocess") / import httpx / import socket
# 放：import pandas as pd + matplotlib
from mmagent.pipeline.runner import run_script   # 返回 returncode/stdout_tail/stderr_tail/duration_s/produced_files

# FR-6 出图：render_figures(...) → PNG dpi 元数据应为 300
# FR-13 合规：make_ai_disclosure(profile_id, version_id, out_dir) → md + docx 双产物
# NFR-7 脱敏：scan_and_mask([...]) -> (masked, hits)；assert_clean(...) 命中即抛 DESENSITIZE_BLOCKED
# 依赖方向自查：tools 不得 import agents；pipeline 不得 import agents（repair 用回调注入 regen_fn）
```

---

## 3. 续作项的具体动手方案

### P0-1 人机回环（最高优先，文档主架构）

- 目标：`coordinator.py` 的三个卡点（口径冲突 / sanity blocking / 导出前合规）改为可暂停可恢复，状态用 SQLite 检查点，进程重启后能续跑（NFR-5 丢失版本 ≤1）。
- 现成骨架（课件）：
  - `C:\Users\Lenovo\Desktop\课件\agent\10_LangGraph入门\demo10_人机回环_键盘输入.py` —— `interrupt({...})` 暂停 + `graph.invoke(Command(resume=...), config)` 恢复；敏感节点单独拆分、resume 后防重入 `if isinstance(last_message, ToolMessage): return {"messages": []}`
  - `...\demo11_存储到SQLite.py` —— `SqliteSaver(conn)` + `builder.compile(checkpointer=memory)` + `memory.get(config)["channel_values"]["messages"]` 读快照
- 依赖：`pip install langgraph langgraph-checkpoint-sqlite`，并写入 `requirements.txt` + lock
- 注意：保留现有纯 Python 路由逻辑（`Stage` 枚举 + 转移表）作为节点内部实现，只把"卡点"换成 interrupt；不要盲目重写整个状态机。

### P0-2 FR-5 端到端可运行率（当前 0/3）

优先级从高到低（前两条最可能直接见效）：
1. **输入文件改 ASCII 名**（`data/att1.xls` 等）—— 模型稳定把 `附件1.xls` 写成 `附件 1.xls`，CJK 空格 quirk 是当前最确定的失败源
2. **few-shot 注入**：在 `prompts/coder.system.j2` 里给 1~2 个正确范式（`pandas.read_excel(..., engine="xlrd")` 取值、`Path` 拼路径、matplotlib 出图保存）
3. **修复轮回传上一版完整源码 + 约束"只输出 code"**（`pipeline/repair.py` 的上下文拼装处）
4. 换更稳的模型或加大 `num_predict`（`OllamaClient.chat` 已支持 `num_predict` + `think`）
5. 基线如实记 0/3，勿沿用文档 ≥70%

### P0-3 golden 真值冻结（NFR-4）

- 已有可复跑独立真值：`fr2_ground_truth.md`（附件1 A1=99.7833/B14=90.3250/D7=81.8500、附件4 合计 432、附件5 合计 131、附件6 定额 27289 / 实际 31344、157 门店、附件2 col24={144,13}、附件3 col24={154,3}）
- 待补：`tests/golden/**` + `SHA256SUMS` + `cli freeze-golden`（禁手填）+ `tests/regression/a_case_set.yaml` + `cli regression run/stats` + `mmagent/metrics.py`
- 文档示例值 `row_count=214 / mapping_rows=63 / conflicts=7` **不可采信**（已穷举证伪），须由 `freeze-golden --write` 依实跑生成后**由何昆阳签署**

### P0-4 FR-9 字段级溯源

`store/schema.sql` 增 `profile_field_trace`（profile_id, version_id, field, producer, produced_at, upstream_refs, review_flag），`artifacts` 表补 `upstream_refs` 列；`store/profile.py::write_version` 同事务写 trace；新增 `mark_review()`；`api/routes_profile.py` 暴露。注意：这会改动已冻结的 `schema.sql`，须重新跑全量测试并更新冻结基线。

### P1-P2

见 `PROGRESS.md §4`。其中 **NFR-9 分级错误码**（SANITY_BLOCKED 422 / IMMUTABLE_VERSION 409 / QUEUE_FULL 429 / LLM_BUDGET_EXCEEDED 499）成本低、对前端演示价值高，建议排在 P1 首位。

---

## 4. 待何昆阳确认的两件事

1. **文档 spotcheck 示例真值（214 / 63 / 7）的出处** —— 已穷举本目录所有文件与口径，0 命中，且示例内部自相矛盾（同一 214 被赋给行数各异的附件1/5/6）。推测可能来自**官方 2024 A 题全量数据**（本目录为"校赛"子集）。若确认如此，golden 真值须以本地数据重新冻结。
2. **附件3 的 17 处文本 `97.3.4` 的正确值** —— 已列清单（`dirty_cells.json`）未自动改正，需业务侧给出正确值（应为 97.3 或 97.4）。

---

## 5. 收尾时的团队动作

- 源码已冻结（8 个 md5，见 `PROGRESS.md §1`）；实现方/测试方已停手
- 若续作需要，可复用三份最值钱的核验材料：`fr9-verification.md`（对抗性攻击 + REPLACE 漏洞取证）、`fr2_ground_truth.md`（独立真值）、`fr2_reverify.md`（复跑比对），三者均附可复跑脚本
- 建议续作开工时先 `git init` 并提交冻结态，把"改动 → 重跑测试 → 更新 md5 基线"固化成流程（本次冲刺出现过基线被反复打破的问题）
