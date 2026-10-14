# PROGRESS.md — task-001「场景一 数学建模竞赛协作 Agent」进度快照

- 记录时点：2026-10-13 20:20（2 小时冲刺收尾）
- 仓库根：`H:\mm-agent`　数据根：`C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题`
- 依据：《场景一-数模竞赛协作Agent-实施规划书》《何昆阳_场景一-数模竞赛协作Agent-项目开发计划书》+ 课件 `C:\Users\Lenovo\Desktop\课件\agent`
- 团队：调查员 Vera / 实现工程师 Max / 测试工程师 Tess（队长统筹与独立复核）
- 全部核验材料在 `.agent-runs/2026-10-13/task-001/`（11 份报告 + 6 个可复跑探针脚本）

---

## 1. 冻结基线（源码已停止改动）

| 文件 | md5 |
|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` |
| `README.md` | `2175bb71faab5b4618373e8ca618fc92` |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` |

- 冻结产物目录：`artifacts/intake/`（8 表 / 153 字段 / 5 冲突），由冻结代码经 `cli intake` 生成
- 其余 32 个源文件未列入者按同批冻结（改动量小、未单列；完整清单见 §2）
- ⚠️ **仓库尚无 `.git`** —— 冻结只能靠上表 md5 清单，无法打 tag。续作第一步建议 `git init` + 提交并打 tag `mm-freeze-2026-10-13`

---

## 2. 交付物

### 2.1 源码（`mmagent/`，40 个 Python 文件 / 2950 行）

```
mmagent/
  __init__.py  app.py(FastAPI 工厂)  cli.py(4 子命令)  plotstyle.py
  store/       db.py  schema.sql  profile.py(FR-9 版本链)
  pipeline/    intake.py(FR-2)  intake_llm.py  guard.py(FR-5 守卫)
               runner.py(FR-5 执行)  repair.py(FR-5 修复)  render.py(FR-6)
  checks/      sanity.py
  compliance/  disclosure.py(FR-13)
  security/    desensitize.py(NFR-7 脱敏)
  agents/      base.py(Ollama 基座)  coordinator.py(状态机)  archivist/planner/coder/assembler.py
               prompts/*.system.j2(4 个)
  tools/       intake/solve/plot/profile/compliance_tool.py(确定性薄封装)
  api/         routes_intake/solve/profile/compliance.py
  rag/         adapter.py(占位)
```

### 2.2 配置（`config/`）

`models.yaml`（Ollama 地址/模型/温度/token 预算）、`intake_scope.yaml`（FR-2 输入白名单）、`field_aliases.yaml`（字段/表别名 + legacy 映射）、`desensitize.yaml`（NFR-7 五类正则，py 内无硬编码）

### 2.3 测试（`tests/`，8 文件 / 2713 行）

`conftest.py`、`test_fr9_profile.py`、`test_fr2_intake.py`、`test_fr2_intake_rework.py`、`test_fr5_guard_runner.py`、`test_sanity.py`、`test_fr6_render.py`、`test_fr13_disclosure.py`

### 2.4 体检产物（`artifacts/intake/`）

`schema_report.json`（含 `physical/effective/blank_rows`、`numeric_sum`、`value_counts`、`skipped_files`）、`caliber_conflicts.json`、`field_mapping.csv`、`unmapped_columns.json`、`dirty_cells.json`、`column_profiles.json`、`skipped_files.json`、`intake_summary.md`

### 2.5 报告（`.agent-runs/2026-10-13/task-001/`）

`plan.md`、`brief-implementer.md`、`brief-test-writer.md`、`investigation-report.md`、`requirement-coverage.md`、`implementer-report.md`、`test-report.md`、`fr2-verification.md`、`fr2_reverify.md`、`fr9-verification.md`、`fr2_ground_truth.md`，另 `docx_dump.txt`（两份需求文档全文，可 grep）

---

## 3. 已完成并通过独立核验的部分

| 需求 | 状态 | 关键证据 |
|---|---|---|
| **FR-9 档案版本链** | ✅ 核心完成 | 草案裁剪恰好 5 版、version_id 写入序列连续、6 进程×5 写=30 个 id 无重复且 1..30 连续（BEGIN IMMEDIATE+MAX+1 成立）、adopted 行 UPDATE/DELETE 被触发器拦、`AdoptedVersionImmutableError` + events 表 `ADOPTED_IMMUTABLE` 留痕、JSON 往返（中文/嵌套/None/emoji）逐键相等 |
| **FR-2 附件体检** | ✅ 主要完成 | 附件1=13 / 附件2=25 / 附件3=25 / 附件4=13 / 附件5=13 / 附件6=37 字段；附件6 dtype 三类(str13/int12/float12)；附件1 有效行 157；附件4 年合计 432、附件5 131、附件6 定额 27289 / 实际 31344（合计 58633）；5 条口径冲突覆盖三类真冲突；输入白名单 7 文件；两个留痕清单齐备 |
| **FR-5 确定性执行** | ⚠️ 机制完成，端到端未绿 | guard 拦 14 类违规（import 白名单 + 危险调用黑名单）、runner 正常/崩溃/超时三态、manifest 校验、repair 一轮闭环机制全通；**真实 Ollama 端到端首次可运行率 0/3** |
| **FR-6 出图** | ✅ | dpi=300（PIL 独立复核）、中文字体 fallback、figure_manifest.csv |
| **FR-13 合规声明** | ✅ | md+docx 双产物、四关键字段、人改比例三种来源、模型/提示词溯源 |
| **NFR-2 零外网** | ✅ | `netguard-check`（AST 检测）通过；guard 拦 `requests/socket/urllib/ftplib/httpx/urllib3/http.client` |
| **NFR-7 脱敏** | ✅ | 5 类正则 20/20 检出、命中即 `DESENSITIZE_BLOCKED`(403)、台账只存 `raw_sha256` 不落原文、列级规则支持 |
| **NFR-3/NFR-8 成本与规模** | ✅ | 核心 2950 行 ≤ 3000 预算；全本地 Ollama，零云支出 |
| **人机回环** | ❌ 未落地 | 见 §4 P0-1 |

**数据核查（sanity）**：得分 0~100 / 占比 0~1 / 利润 ≥0 / 主键唯一 / not_null / row_count_match，越界判定已剔除 NaN。

### 3.1 测试最终结果（2026-10-13 20:34–20:35，冻结基线上跑）

```
cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q
→ 189 passed, 1 xfailed in 48.62s     （共 190 例，0 failed / 0 skipped）
```

| 文件 | 覆盖 | 例数 | 结果 |
|---|---|---|---|
| `test_fr9_profile.py` | FR-9 | 32 | 31 passed, 1 xfailed |
| `test_fr2_intake.py` | FR-2 | 51 | 51 passed |
| `test_fr5_guard_runner.py` | FR-5 | 43 | 43 passed |
| `test_sanity.py` | 数据核查 | 24 | 24 passed |
| `test_fr6_render.py` | FR-6 | 19 | 19 passed |
| `test_fr13_disclosure.py` | FR-13 | 21 | 21 passed |

- `test_fr2_intake_rework.py`（17 条预备用例）已并入 `test_fr2_intake.py` 转正为绿后删除，避免两套口径
- golden 已**直接断言**：`numeric_sum` 附件4=432.0 / 附件5=131.0 / 附件6 定额=27289.0 / 实际=31344.0（合计 58633.0，`approx(abs=1e-6)`）；`value_counts` 附件2 M12 评级 {合格:144,不合格:13} / 附件3 {154,3}；字段数精确真值 13/25/25/13/13/37；三口径 附件1=160/157/3、附件6=157/157/0；`unmapped_count==2`、`dirty_cells==17`
- 第 0 节冻结基线覆盖 45 个文件，机器可读副本 `.agent-runs/2026-10-13/task-001/md5-baseline-final.md`（20:38 复核无漂移）。**仅代表 20:35 该时点**，任一 md5 不符则测试结论失效需重跑（复跑命令见 test-report.md §6）

**仍开放的 1 条 xfail（ISSUE-FR9-03，审计链缺环）**：裸 SQL `INSERT OR REPLACE` 打 adopted 行时，被 BEFORE INSERT 触发器 `RAISE(ABORT)` 拦下（**数据安全已保住**），但因 ABORT 直接回滚、应用层无机会写入，`events` 表**无违规留痕**。建议续作在写路径统一捕获 `IntegrityError('adopted immutable')` 补记事件，或由触发器写独立违规台账。

---

## 4. 未完成 / 续作项（按建议优先级）

### P0（不补就有"声明的能力不存在"的风险）

1. **人机回环（LangGraph `interrupt()` + `SqliteSaver`）** —— 文档主架构是课件**7 号 Graph/Workflow**，三个卡点（口径冲突僵持 / sanity blocking / 导出前合规）必须能暂停并恢复（NFR-5 丢失版本 ≤1）。现状 `coordinator.py` 只是纯 Python 状态机 + `HumanGateRequired` 异常占位，**暂停后无法恢复**；`requirements.txt` 无 langgraph。课件 `10_LangGraph入门/demo10`（interrupt + Command(resume)）与 `demo11`（SqliteSaver 检查点）有现成骨架可直接改。
2. **FR-5 端到端可运行率 0/3** —— 机制全通，但 qwen3.5 连续 3 次生成不可运行脚本（`'Cell' object has no attribute 'strip'`、以文本模式读 .xls），修复轮也未纠正；模型路径 quirk 稳定把 `附件1.xls` 写成 `附件 1.xls`。已修的两个严重缺陷（runner 相对路径二次拼接、OllamaClient 缺 `num_predict` 且未处理 `thinking` 字段 → JSON 模式长输出必失败）已落地。建议：① 输入文件改 ASCII 名（`att1.xls`）；② 把 xlrd 取值/路径写法做成 few-shot 注入；③ 修复轮回传上一版完整源码并约束"只输出 code"；④ 基线如实记 0/3，勿沿用文档 ≥70% 假设。
3. **NFR-4 真值冻结与回归集** —— 文档纪律"未对应测试用例的 P0 不得关闭"。现状已有可复跑的独立真值（`fr2_ground_truth.md`）与 192 例测试，但缺 `tests/golden/**` + SHA256SUMS + `cli freeze-golden` + `tests/regression/a_case_set.yaml` + `cli regression run/stats` + `mmagent/metrics.py`。
4. **FR-9 字段级溯源 `profile_field_trace` + `review_flag`** —— 字段级 producer/time/version_id/upstream_refs 是 FR-9 与 FR-13 的共同基座；`artifacts` 表也缺 `upstream_refs` 列。
5. **ISSUE-FR9-03 违规审计链缺环**（唯一的 xfail，见 §3.1）—— 裸 SQL REPLACE 被触发器拦下但 `events` 无留痕；在写路径捕获 `IntegrityError('adopted immutable')` 补记事件即可闭合。

### P1

5. **FR-1 题面解析**（archivist 真正接 LLM；现输出 schema 与文档五键 schema 不符）
6. **NFR-9 队列与分级错误码** —— 文档 26 条路由 / 9 个错误码，现 5 条路由；`SANITY_BLOCKED(422)` / `IMMUTABLE_VERSION(409)` / `QUEUE_FULL(429)` / `LLM_BUDGET_EXCEEDED(499)` 未映射
7. **NFR-10 离线部署与 lock** —— `deploy/wheelhouse/`、`scripts/01..05*.ps1`、`requirements.lock.txt`、`cli env-attest --verify`（退出码 33）
8. **guard 剩余 TODO** —— `open()` 写路径白名单、`while True` 死循环检测（`mmagent/pipeline/guard.py`）
9. **`intake_llm.explain_conflicts`** 接入 `agents.base.RoleRunner('archivist')`

### P2

10. FR-3 四类模型卡片（AHP/熵权TOPSIS/MLR/LP）+ `data_check` 硬校验（planner"明显错配数 0"）
11. FR-8 进度守卫 `pipeline/watchdog.py` + `config/time_budget.yaml`（零 LLM 成本，对应急救场景）
12. FR-7 论文装配 / FR-10 编号一致性 / FR-12 导出打包
13. FR-4 检索（`rag/adapter.py` + `<RAG_ROOT>` 回填 + `source_ledger` 表）
14. NFR-6 前端 7 个演示页 + 可用性测试记录
15. 工程骨架收尾：`git init`、`pyproject.toml`、`scripts/code_freeze_check.ps1`、`docs/`（RULE_JUDGEMENT / AI_DISCLOSURE_TEMPLATE / BASELINE_REPORT）

---

## 5. 已知偏差（文档 vs 实现，均为已记录的降级）

| 项 | 文档 | 实际 | 处理 |
|---|---|---|---|
| Python 版本 | 3.11.9 | **3.13.12**（本机无 3.11） | 记入已知偏差；NFR-10 lock 未做 |
| 降级模型 | `qwen3:4b` | **`qwen3.5:0.8b`**（本机无 qwen3:4b） | 降级链改为 qwen3.5:latest → qwen3.5:0.8b |
| embedding | `qwen3-embedding` | `qwen3-embedding:latest`（Ollama 自动补 :latest） | RAG 启用时注意 |
| spotcheck 示例真值 | `row_count=214`、`mapping_rows=63`、`conflicts=7` | **本目录不可推出** | 穷举 9 文件/18 sheet/154 组合 → 0 个 =214、0 个 =63；同一 214 被同时赋给行数各异的附件1/5/6；row_count 与 missing_rate 同行互斥；文档自述"不得手填猜测值"→ **判定示例不自洽**。假设（未证实）：214 可能来自官方 2024 A 题全量数据，本目录为"校赛"子集 —— **待何昆阳确认** |
| table_id 命名 | 文档仅显式定义 `att1_satisfaction`/`att5_complaint`/`att6_profit` | `att2_service`/`att3_clean`/`att4_complaint` 为**按同构规则外推** | README §7 已注明；`legacy_table_ids` 兜底 |
| P3 端到端 | 文档假设 ≥70% 首次可运行率 | **实测 0/3** | 如实记入报告，未调 prompt 粉饰 |
| 版本控制 | code freeze tag `mm-freeze-<赛日>` | **仓库无 `.git`** | 改用 md5 冻结清单（§1） |

---

## 6. 已知数据问题（原始附件，供下游避坑）

1. **附件1 门店顺序与其他附件不同**（A1..A5,B14,B37,B70,B56,B108,B7,B91… 乱序；附件2~6 是 A1..A5,B1..B7）→ **跨表汇总一律按门店 id join，禁止按行号对齐**（已写入 README §8）
2. **附件6 定额列存在逐行全等组**：`{月1,月2}`、`{月3,月11}`、`{月7,月8}`（实际利润列**无**全等组）→ 逐月独立回归会因定额共线得出虚假显著系数；另评级列有全等组 `{月1,月2,月7,月8,月10}`
3. **附件3 有 17 处文本 `97.3.4`**（10/11/12 月得分列，T:7 / V:5 / X:5）→ 真实录入事故，产物已进 `dirty_cells.json` 带行列坐标，**未自动改正**，待业务侧确认
4. **附件4/5 标题声明「空格处为0」** → 空格按 0 计，非缺失（`blank_as_zero` / `fillna_rule`）；附件5 的 `1月`/`4月` 为整列全空，已标 `all_blank_as_zero`
5. **附件1 第 N 列是"年度均值派生列"**（= 该行 12 月得分算术平均，最大误差 2.8e-14）→ 已进 `unmapped_columns.json` 并标 `derived`
6. **附件6 row130** 有 2 处 dup_group 标注导致 numeric_sum 比独立真值多 3 元（0.011%）→ 已知差异
7. 附件1 有 3 个整行全空的分区隔行（row7/128/151）→ 有效门店行 157（`effective_rows`）

---

## 7. 关键决策记录

1. **表头解析弃用 `last_non_empty` 边界、改按 merged_cells 分组归属** —— 原逻辑使"末列表头为空但下方有数据"的列既不填充也不建字段，一个 bug 同时让附件2/3 各丢 1 列、附件6 丢 2 列
2. **口径冲突的跨 sheet 比较单元从 `table_id` 改为 `(file, sheet)`** —— 原规则要求跨不同 table_id，导致同一文件内「确定资格（文本）合格/不合格」vs「（数值）1/0」的真实冲突全部漏检
3. **不为凑数放宽冲突词表** —— `period`/`entity_scope` 在真实数据 0 命中时如实记录，不造假冲突（冲突数 0→5 全部可核查，每条带源文件/sheet/字段/dtype/样例）
4. **脏数据只留痕不自动改** —— 附件3 的 `97.3.4` 只出清单，交业务侧确认
5. **FR-2 输入白名单 = 总表.xlsx + 附件1~6（7 文件）** —— 文档把预处理 xlsx 定义为参考材料；排除项记入 `skipped_files` 保证 golden 可复现
6. **`read_profile(profile_id,'last')` 口径裁定** —— 优先返回 version_id 最大的 **adopted**；无 adopted 时退化为最大 draft；返回值带 `status`
7. **guard 由"黑名单"改为"import 白名单"** —— 原实现可被 `from os import system` / `importlib.import_module("subprocess")` 绕过，且漏了 httpx/urllib3
8. **FR-9 不可变性加 `BEFORE INSERT` 守卫触发器 + `conn` 开 `recursive_triggers=ON`** —— `INSERT OR REPLACE` 靠隐式 DELETE 解决主键冲突，而隐式 DELETE 默认不触发 BEFORE DELETE 触发器，可原地覆盖已 adopted 行且无留痕
9. **LLM 通信走 httpx 直连 Ollama `/api/chat`**（而非 OpenAI 兼容端点）—— `think:false` + 传 `num_predict`，否则思考型模型输出全落在 `message.thinking`、`content` 恒空
10. **P3 冒烟失败不调 prompt 粉饰** —— 如实记录 0/3 与失败原因，改进方向交续作

---

## 8. 续作入口

见同目录 `final-summary.md`（命令速查 + 每个模块如何自测 + 续作第一步）。

---

## 9. 收尾核验补充（2026-10-13 20:54 独立复算追加）

> 本节为收尾核验追加，**未改动上文任何内容**；冻结源码与产物本次均未变更，仅只读复算 + `pytest --collect-only`。

### 9.1 冻结基线复算 —— 8/8 一致，无文件被改

在仓库根 `H:\mm-agent` 用 `md5sum` 逐项与 §1 比对，**8 项全部命中，零漂移**：

| 文件 | §1 md5 | 20:54 复算 | 判定 |
|---|---|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` | 同 | ✅ |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` | 同 | ✅ |
| `README.md` | `2175bb71faab5b4618373e8ca618fc92` | 同 | ✅ |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` | 同 | ✅ |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` | 同 | ✅ |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` | 同 | ✅ |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` | 同 | ✅ |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` | 同 | ✅ |

另对 `md5-baseline-final.md` 的 **45 行**全量基线（md5 + mtime + 字节）逐行复算：**0 处漂移**（含 4 个 `config/*.yaml`、`store/schema.sql`、`mmagent/**.py`）。结论：**冲刺冻结后无人改动源码/配置**。

### 9.2 产物核验 —— 与 §1/§3 声明一致

| 核验项 | 声明 | 磁盘实测 | 判定 |
|---|---|---|---|
| `artifacts/intake/` 存在 | 是 | 存在，8 个文件 | ✅ |
| `schema_report.json` tables | 8 | 8 | ✅ |
| `schema_report.json` fields | 153 | 153（= 各表 `field_count` 之和） | ✅ |
| `caliber_conflicts` count | 5 | 5 | ✅ |

- **措辞精确化**：`caliber_conflicts` **不在** `schema_report.json` 内（该文件顶层键仅 `tables` / `fields` / `skipped_files`），实际位于同级 `artifacts/intake/caliber_conflicts.json`（`count=5`，`conflicts` 数组长度 5）；`intake_summary.md` 亦复述「口径冲突：5 条」。上文 §3 的「5 条口径冲突」表述本身无误。
- **独立复算表级 `numeric_sum`**（不依赖报告自证）：附件4 = **432.0**、附件5 = **131.0**、附件6 = 定额 **27289.0** + 实际利润 **31344.0** = **58633.0** —— 与 §3 逐值一致。
- 留痕清单：`skipped_files = 2`、`unmapped_columns.count = 2`、`dirty_cells.count = 17` —— 与 §3.1 golden 断言一致。
- 源码规模：`mmagent/**.py` = **40 文件 / 2950 行** ≤ 3000 预算 —— 与 §2.1 一致。

### 9.3 测试结论核对

- `pytest tests --collect-only` = **190 例**（仅收集、不执行，避免触动冻结产物）。
- `test-report.md` **已回填**最终结果（RUN#2 20:39–20:40：`189 passed, 1 xfailed in 43.73s`，0 failed / 0 skipped，附逐文件 32/51/43/24/19/21），**未缺漏**。

### 9.4 发现的与磁盘不符条目（建议续作时订正，本次未改写上文）

| # | 位置 | 现有记述 | 磁盘实测 | 性质 |
|---|---|---|---|---|
| A | §2.3 | 测试「**8 文件 / 2713 行**」，并列出 `test_fr2_intake_rework.py` | **7 文件 / 2568 行**（`conftest.py` + 6 个 `test_*.py`）；`test_fr2_intake_rework.py` 已删除（§3.1 自身亦如此记载） | 陈旧条目，同文档内自相矛盾 |
| B | §4 P0-3 | 「与 **192 例**测试」 | **190 例**（与 §3.1 一致） | 数字笔误 |
| C | §6 第 6 条 | 「附件6 row130 有 2 处 dup_group 标注导致 numeric_sum 比独立真值**多 3 元（0.011%）**」 | att6 `numeric_sum` 合计 **= 58633.0，与独立真值逐值相等，无 +3 元**；且 `dup_group` 是**字段级**标注（att6 共 11 字段 / 4 组：`def_1_2`、`def_3_11`、`def_7_8`、`qual_1_2_7_8_10`），**不存在 row130 这类行级标注** | **与磁盘不符**，建议删除或改述 |
| D | §1 末注释 | 「其余 **32 个**源文件未列入者按同批冻结」 | `md5-baseline-final.md` 共 **45 项**，与本表 8 项重合 7 项 → 其余应为 **38 项**（若按「除本表 8 项」计则 37） | 计数不符，建议改为「其余 38 个文件见 `md5-baseline-final.md`（共 45 项）」 |
| E | §3.1 | 「**第 0 节**冻结基线覆盖 45 个文件」 | PROGRESS 无「第 0 节」；45 项清单在独立文件 `md5-baseline-final.md`（该文件亦无节号） | 交叉引用笔误 |
| F | §2.5 | 报告清单列 11 份 | 另有 `md5-baseline-final.md`（§3.1 已提及，但 §2.5 未列入） | 清单不全（轻微） |

补充说明（属**已更正的陈旧项**，非新问题）：
- `fr2_reverify.md` §0/§3 判「附件5 的 1月/4月 整列全空未套用空格=0 = 唯一残留（6.5/7）」——该残留已被**修复轮 3.2** 修掉：现产物这两列 `missing_rate=0.0`、`blank_as_zero=True`、`all_blank_as_zero=True`，`intake_summary.md` 的 Top10 已改用 post-fillna 口径。该报告的「6.5/7」为其时点快照，相对冻结产物已过期。
- `fr2-verification.md` §4.3 括注的定额重复列组仍写「3月=4月=11月、5月=9月」，已被 `fr2_ground_truth.md` §2.2 更正为 `{月1,月2}` / `{月3,月11}` / `{月7,月8}`，且与产物 `dup_group` 一致；早期报告未回改。

### 9.5 核验结论

**冻结基线与产物全部自洽，源码/配置/产物零漂移；`PROGRESS.md` 有 6 处文档层记述与磁盘不符（A–F），均不影响代码与产物的有效性。** 本次仅核验：未改动任何业务代码、未改动冻结文件、未重跑全量测试（仅 `--collect-only`）。

---

## 10. task-002 后冻结基线更新（2026-10-14 追加，未改动上文）

> 本节为 task-002「人机回环（LangGraph interrupt + SqliteSaver）」交付后追加。§1「8 项终版基线」中
> **`README.md` 已因 task-002 的文档更新而失效**，故发布**新基线**；其余 7 项沿用 §1。

### 10.1 新基线（2026-10-14 磁盘实测）

| 文件 | §1 基线 md5 | 现 md5 | 判定 |
|---|---|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` | 同 | ✅ 未变 |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` | 同 | ✅ 未变 |
| `README.md` | `2175bb71faab5b4618373e8ca618fc92` | **`361afccc9e0d0262604d5c4887ff454a`** | ⚠️ **有意变更** |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` | 同 | ✅ 未变 |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` | 同 | ✅ 未变 |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` | 同 | ✅ 未变 |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` | 同 | ✅ 未变 |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` | 同 | ✅ 未变 |

**7/8 一字未变**；仅 `README.md` 有意变更（task-002 DoD#6）。

### 10.2 README.md 变更内容（为何必须重发基线）

- 新增「7. 人机回环（LangGraph）」小节 + §7.5–7.9：裁定→去向语义表（含 `HALTED` 终态）/ payload `review` 键 /
  澄清课件 P2「`interrupt()` 保存完整状态」的**不成立**说法 / 有意不用课件 `SqliteSaver.from_conn_string`
  的理由 / 口径冲突取值域映射表（docx `use_a/use_b/third_party` ↔ 实现 `use_mapping/keep_raw/human_override`，标注待用户确认偏差）。
- Python 版本 `3.13.12` → **`3.13.14`**（实测）；章节编号顺延（原 7/8 → 8/9）。

### 10.3 新基线生效值

- **`README.md` = `361afccc9e0d0262604d5c4887ff454a`**（其余 7 项沿用 §1）。
- 其余 7 个源文件（含 `schema.sql`）一字未改 → task-001 的 FR-2 / FR-5 / FR-6 / FR-9 / NFR-2 / NFR-7 冻结语义不受影响。
- 本节由 task-002 收尾（Phase 4 独立验证）追加；task-002 已提交（commit 见 task-002 `final-summary.md`）。
