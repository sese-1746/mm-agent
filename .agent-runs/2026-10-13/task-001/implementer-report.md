# 实现报告 — task-001 场景一数模协作 Agent（Phase 2 / 实现工程师 Max）

- 日期：2026-10-13
- 仓库根：`H:\mm-agent`（全本地、零云支出）
- 授权范围：`mmagent/`、`config/`、`requirements.txt`、`README.md`（+ 本报告文件）
- Python：`H:\mm-agent\.venv\Scripts\python.exe`（3.13.14）

## FINAL STATUS: PASS（本轮范围全部落地并冒烟通过；LLM/RAG 相关为骨架，见「未完成项」）

自检证据（均为本轮实跑）：
- import 冒烟：`smoke ok`（含 app/cli/tools/api/rag 全量 import）
- `python -m mmagent.cli netguard-check` → 通过，exit 0
- `python -m mmagent.cli db init --db data\mm.db` → 已初始化，exit 0
- `python -m mmagent.cli env-attest --out artifacts\reports\env_attest.json` → ollama available=True（本机已连上 localhost:11434）
- `python -m mmagent.cli intake --dataset "<A题目录>" --out artifacts\intake` → 表数=18 字段数=323 冲突数=0
- 核心功能实测：FR-9 版本链（草案裁剪到 5 版、adopted 不可变拦截 + error 事件）、guard 拦截 `os.system`、runner manifest 校验、sanity 越界拦截、render dpi=300、disclosure md+docx 生成 —— 全部通过
- FastAPI TestClient：health 200、404/422 均返回统一错误体 `{error_code,message,detail,trace_id}`

## CHANGED_FILES

依赖方向：`api → agents → tools → pipeline → store`；`tools` 不 import `agents`；`store`/`checks` 不 import `agents`/`api`。

| 文件 | 说明 |
|---|---|
| `mmagent/__init__.py` | 包版本 `0.1.0` + 依赖方向说明 |
| `mmagent/app.py` | `create_app()` FastAPI 工厂；4 业务路由 + `/api/v1/health`；统一错误体 |
| `mmagent/cli.py` | argparse：`db init` / `intake` / `env-attest` / `netguard-check` |
| `mmagent/plotstyle.py` | 论文出图风格（SimSun/SimHei/雅黑 fallback、dpi300、unicode_minus=False）+ `available_cjk_font()` |
| `mmagent/store/db.py` | 连接工厂（WAL / foreign_keys=ON / busy_timeout=5000）+ `init_db` / `utcnow` |
| `mmagent/store/schema.sql` | profile_doc / events / artifacts / metric_obs + adopted 不可变触发器（UPDATE/DELETE 双保险） |
| `mmagent/store/profile.py` | FR-9：`write_version` / `adopt_new_version` / `read_profile` / `AdoptedVersionImmutableError` / `try_update_row`（演示用） |
| `mmagent/pipeline/intake.py` | FR-2：`run_intake_audit` + 表头行探测 + 四类口径冲突启发式 + 4 件产物 |
| `mmagent/pipeline/intake_llm.py` | `explain_conflicts` 骨架（无 LLM 时模板化降级） |
| `mmagent/pipeline/guard.py` | FR-5：`validate_script_ast`（AST 级禁 import/禁调用） |
| `mmagent/pipeline/runner.py` | FR-5：`run_script` 子进程 + 超时 + manifest 校验 |
| `mmagent/pipeline/repair.py` | 修复回路骨架（接口 + TODO 接入点） |
| `mmagent/pipeline/render.py` | FR-6：`render_figures` + `FigureManifest` + PNG dpi 元数据校验 |
| `mmagent/checks/sanity.py` | 内置规则（得分/占比/利润）+ 自定义规则 + `SanityReport` |
| `mmagent/compliance/disclosure.py` | FR-13：`make_ai_disclosure`（md + docx，四字段，人改比例计算） |
| `mmagent/agents/base.py` | OllamaClient（httpx，base_url 来自 config）+ RoleRunner（JSON 强制→降温重试→降级→schema 校验→观测落库）+ `strip_code_fence` |
| `mmagent/agents/coordinator.py` | 阶段枚举 + 转移表 + `step()` + 3 个人工卡点 `HumanGateRequired` + 连续失败 2 次转人工 |
| `mmagent/agents/{archivist,planner,coder,assembler}.py` | 4 角色 prompt 版本常量 + 薄封装 |
| `mmagent/agents/prompts/*.system.j2` | 4 个角色 system 模板（jinja2） |
| `mmagent/tools/{intake,solve,plot,profile,compliance}_tool.py` | 纯确定性薄封装（无新增逻辑） |
| `mmagent/api/routes_{intake,solve,profile,compliance}.py` | 4 个 APIRouter（prefix `/api/v1`），只调 tools 层 |
| `mmagent/rag/{__init__.py,adapter.py}` | RAG 占位；adapter 声明为全仓库唯一允许引用 `RAG_ROOT` 的模块，`search()` 抛 NotImplementedError |
| `config/models.yaml` | ollama base_url、生成/降级/embedding 模型、4 角色温度与预算 |
| `requirements.txt` | 12 项依赖按 .venv 实装版本锁定 |
| `README.md` | 快速开始（环境 / Ollama 前置 / 命令序列 / 接口表 / 架构 / KNOWN_ISSUES） |

运行时产物（由 CLI 指令产生，非源码）：`data/mm.db`、`artifacts/intake/*`、`artifacts/reports/env_attest.json`。

## 各模块要点

### FR-9 档案版本链（最高优先级，完整可测）
- `write_version`：单事务 `BEGIN IMMEDIATE` 内 `SELECT MAX(version_id)+1`；写入 `status='draft'`；写事件；**草案裁剪保留最近 5 版**（`DRAFT_KEEP=5`）；锁冲突重试 3 次、退避 100ms。
- `adopt_new_version`：只 INSERT 新行（`status='adopted'`、`supersedes=from_version_id`），**永不 UPDATE adopted 行**。旧 adopted 版本靠 version_id 比较自然退位（`status='superseded'` 为 CHECK 预留）。
- 触发器 `trg_profile_adopted_immutable`（BEFORE UPDATE）/`trg_profile_adopted_nodelete`（BEFORE DELETE）抛 `adopted immutable`；`_convert_integrity` 转为 `AdoptedVersionImmutableError` 并写事件 `error_code='ADOPTED_IMMUTABLE'`。
- 实测：写 6 个 draft → 裁剪后保留 v2..v6；adopt v6 → 新 adopted v7；对 v7 执行 UPDATE 被拦截且 error 事件落库。

### FR-2 附件体检
- **表头行探测**（本轮新增，关键修复）：真实附件是「第 0 行标题、第 1 行表头」的两行结构，原按首行当表头会得到字段数=1。现取前 5 行中第一个非空单元格 ≥2 的行为表头，其上各行文本合并为 `table_title`；分组表头空位前向填充、重名列加 `#2` 后缀。修复后 18 表 / 323 字段（修复前 19 表 / 161 字段且多数表只有 1 个字段）。
- 冲突启发式四类：unit（括号单位不一致）、period（周期词不一致）、entity_scope（范围词不一致）、definition（同名跨表 dtype 不一致）；纯数字表头（年份/序号）跳过以减少噪声。
- 产物：`schema_report.json`、`caliber_conflicts.json`、`field_mapping.csv`（confirmed_by/at 留空待人工）、`intake_summary.md`。
- 实测：真实 A 题目录 0 冲突（字段名一致）；合成数据可正确检出 unit/period/entity_scope 三类。

### FR-5 确定性件
- guard：禁 import `requests/socket/urllib/ftplib/subprocess`；禁调用 `os.system/os.popen/subprocess.*/eval/exec/__import__/compile`；报错含行号。
- runner：`subprocess.run([sys.executable, script], cwd=workdir, capture_output, timeout)`；成功 = returncode 0 且 manifest 声明文件齐全；无 manifest 时兜底扫描产物后缀。
- sanity：内置关键词语法（得分 0~100、占比 0~1、利润 ≥0）+ 自定义 `range/not_null/unique/row_count_match`；`blocking` 标记。

### FR-6 / FR-13
- render：Agg 后端 + rcParams 全局风格；每图 `savefig(dpi=300)` 后用 PIL 读回 PNG dpi 元数据校验；输出 `figure_manifest.csv`。
- disclosure：md + docx 四字段；人改比例优先取调用方传值，否则按 `events` 中 `actor LIKE 'human%'` 占比计算，无数据用保守默认 0.3 并注明来源；模型/提示词版本从 `artifacts`/`metric_obs` 查询，查不到写占位。

### agents / API / CLI
- RoleRunner 三级降级链：主模型(角色温度) → 主模型(0.0) → fallback(0.0)，全程仅连 `config/models.yaml` 的 base_url；每次调用尽力写 `metric_obs(llm_latency)` 与 `artifacts(llm_output)`。
- coordinator：`step()` 先查 3 卡点（口径冲突未确认 / sanity blocking / 导出前合规），命中则置 `HALTED` 并 `raise HumanGateRequired`，注释标注对应 LangGraph `interrupt()` 位置。
- API 统一错误体已覆盖 404/422/500 与脚本守卫违规（`SCRIPT_GUARD_VIOLATION`）。

## 自查结果（对照 success_criteria）

| 项 | 结果 | 证据 |
|---|---|---|
| FR-9 版本链 + 触发器 + 异常转换 + 草案裁剪 | PASS | 实测输出见上 |
| FR-2 体检 4 件产物（真实 .xls/.xlsx） | PASS | 18 表 / 323 字段 / 4 文件已生成 |
| guard AST 拦截 | PASS | `import os; os.system('dir')` 被拦截 |
| runner 执行与 manifest | PASS | ok=True, produced_files=['o.txt'] |
| sanity 阻断 | PASS | 得分 120 / 占比 1.5 被标记 blocking |
| render dpi300 + manifest | PASS | dpi=300，无字体告警 |
| disclosure md+docx | PASS | 两文件生成 |
| agents/base + coordinator | PASS（骨架可 import、逻辑纯 Python 可测） | 未做真实 LLM 调用验证 |
| api/cli 可运行 | PASS | TestClient + 4 条 CLI 子命令实跑 |
| 依赖方向 / 全本地（netguard） | PASS | netguard-check exit 0 |
| import 冒烟 | PASS | `smoke ok` |

## 已知风险 / 给 Tess 的交接提示

1. **FR-9 并发**：`write_version` 的 retry 仅覆盖 `OperationalError`（锁竞争）；多进程并发下 version_id 分配靠 `BEGIN IMMEDIATE` 串行化，单测建议用线程/进程并发写验证不跳号。
2. **触发器的 UPDATE 路径**：正常业务路径不会 UPDATE adopted 行，`try_update_row` 是**专供测试/演示**直接触发触发器用的钩子；Tess 用它验证 `AdoptedVersionImmutableError` 与 error 事件最直接。
3. **`read_profile(version='last')`**：无 adopted 时退化为「最新任意状态」，这是有意设计（便于草案期读取）；若测试期望无 adopted 即抛错，需与队长确认口径。
4. **intake 表头探测**：真实附件为两行表头，当前只重建单层表头（分组表头用前向填充 + `#2` 去重），**未做多级表头语义合并**（如 附件2 的「月份×指标」两级结构，字段名表现为 `1.0` 与 `1.0#2`）。这是已知局限，若竞赛交付需要语义化列名需后续增强。
5. **intake 冲突启发式**：仅关键词/括号启发式，真实数据 0 命中不代表无口径风险；`field_mapping.csv` 的 confirmed_by/confirmed_at 留待人工确认环节回填。
6. **纯数字表头**（总表.xlsx 的 `1..12`）会被 definition 类冲突过滤器跳过，避免噪声，但也会漏掉这类表的真实类型不一致。
7. **LLM 相关未做真实调用验证**（本轮明确为骨架）：RoleRunner 的重试/降级、jinja2 模板渲染、coordinator 卡点在无 Ollama 时只能做 mock 测试；本机 Ollama 在线（已由 env-attest 确认），Tess 可用 `qwen3.5:0.8b` 做低成本集成测试。
8. **`qwen3-embedding` 命名**：本机 Ollama 实际标签为 `qwen3-embedding:latest`（无 tag 时 Ollama 默认解析到 `:latest`），配置按任务书写 `qwen3-embedding`；RAG 启用时如有异常优先核对此处。
9. **未跑 pytest**（按指令留给 Phase 3/4）；`repair.py` 抛 NotImplementedError 属预期骨架行为，测试请勿断言其成功。

## 未完成项（骨架 / TODO）

1. `pipeline/repair.py`：`repair_once` 仅接口 + TODO（需接 agents.coder）。
2. `pipeline/intake_llm.py`：`explain_conflicts` 模板化降级，未接 LLM。
3. `rag/adapter.py`：`search()` 抛 NotImplementedError（占位，接入点已注释）。
4. `agents/coordinator.py`：3 个 interrupt 点为异常占位，未接 LangGraph 持久化恢复。
5. 多级表头语义合并、`runner` 白名单路径写入检查（guard 本轮未实现 open 写路径白名单）、`while True` 死循环检测 —— 均按任务书「本轮可略」处理。
6. 未创建 `tests/`（Tess 负责）、未跑 pytest（Vic 负责）。

---

# 第二批（Phase 2b）增量

## P1 NFR-7 脱敏 —— 状态：**DONE（20/20 检出）**

CHANGED_FILES（增量）
| 文件 | 说明 |
|---|---|
| `config/desensitize.yaml` | 5 类规则（`phone`/`student_id`/`id_card`/`name_column`/`advisor`）的 name/pattern/contexts/mask，正则**全部在此文件**，py 中无硬编码 |
| `mmagent/security/__init__.py` | security 层包声明 |
| `mmagent/security/desensitize.py` | `load_rules` / `scan_and_mask` / `scan_column` / `assert_clean` / `record_hits` / `DesensitizeBlocked`(error_code=DESENSITIZE_BLOCKED) |
| `mmagent/store/schema.sql` | 新增 `desensitize_hit` 表（id, ts, rule, table_id, field_raw, masked_value, action, trace_id, raw_sha256） |
| `mmagent/app.py` | 新增 `DesensitizeBlocked` 异常处理器（403 + DESENSITIZE_BLOCKED）并补 HTTP 映射分支 |
| `mmagent/cli.py` | 新增 `desensitize-check --input <file> [--field --table --mask-out --db]` |

要点
- `scan_and_mask(cells, field_raw="", table_id="") -> (masked_cells, hits)`：命中即把**整个单元格**替换为 `[已脱敏]`；hit 结构 `{rule, rule_label, table_id, field_raw, masked_value, raw_sha256, action}`，**只存原文 sha256 前 16 位，绝不落原文**（与需求 DDL 注释「只存哈希，不存原文」一致）。
- 规则两类：`target: cell`（逐单元格正则，`contexts` 用于降低误报，如学号需同时含「学号/学籍号/学生编号」）；`target: column`（列头命中 → 整列脱敏、列头保留）。
- 同一单元格只记首个命中规则（按 yaml 顺序，`id_card` 在 `phone` 前，避免 18 位身份证被手机号规则误分类）。
- `assert_clean(text)`：命中即抛 `DesensitizeBlocked`（`error_code="DESENSITIZE_BLOCKED"`），并把命中写台账，供入库前钩子直接调用。

验收自测（实跑，样本文件 `artifacts/reports/desensitize_samples.txt`）
- **5 类各 4 条，20/20 全部检出**（逐条明细：phone 4/4、id_card 4/4、student_id 4/4、name_column 4/4、advisor 4/4）。
- 命中示例：`['姓名','张伟','李娜'] → ['姓名','[已脱敏]','[已脱敏]']`（列级规则保留列头）；`13812345678 → [已脱敏]`。
- 命中总数 21（name_column 列级规则一列产 2 条），台账落库 21 条；**原文泄漏校验 = 0**。
- `assert_clean("队员张伟的手机号 13812345678")` → 抛 `DESENSITIZE_BLOCKED`；`assert_clean("2024年A题各门店满意度综合得分")` → 正常放行（无误报）。
- CLI：`python -m mmagent.cli desensitize-check --input artifacts\reports\desensitize_samples.txt --db data\mm.db --mask-out artifacts\reports\desensitize_masked.txt` → 命中 18 处（按行扫描，列级规则需列头同行）、exit=1、台账写入成功。

已知局限（已写进给 Tess 的说明）
- `desensitize-check` 是**按行**扫描：列级 `name_column` 规则要求列头与数据同行才生效（列头单独成行时会漏「张伟/李娜」两条），入库钩子请优先用 `scan_column(field_raw, cells)` 按列调用。
- `student_id` 依赖关键词上下文，无「学号/学籍号」等词时不会命中（这是刻意的降误报设计）；若要求纯数字学号无上下文也命中，需放宽 `contexts`。

## P2 FR-2 收尾 —— 状态：**DONE（真实数据 12 条可核查冲突 / canonical 与 fillna_rule 落地）**

实测现状与队长给的数一致：附件1=13 字段/160 行、附件2=24/158、附件3=24/157、附件4=13/157、附件5=13/157、附件6=35/158（表头逻辑未动）。

CHANGED_FILES（增量）
| 文件 | 说明 |
|---|---|
| `config/field_aliases.yaml` | 别名表：`subs`（行内二级语义）/`tables`（源文件表级）/`fields`（列名关键词）/`fillna_hints`（空缺语义） |
| `mmagent/pipeline/intake.py` | canonical / period / entity_scope / fillna_rule 解析；`_detect_conflicts` 重写（跨 sheet 比较 + 表级单位 + 分组表头丢失 + 4 必需键 + 聚合）；`_looks_like_unit`；summary 增补口径来源与 canonical 说明 |

### 1) field_mapping.csv 新列
`table_id, field_raw, field_norm, canonical, unit, period, entity_scope, fillna_rule, source_file, source_sheet, confirmed_by, confirmed_at`

canonical 实测（符合指定口径，月份列带 `_mNN` 期号）
- 附件1 综合得分 → `satisfaction_overall_m01..m12`
- 附件2 得分 → `service_score_mNN`；附件2 的配对「合格」列 → `service_score_qualified_mNN`
- 附件3 得分 → `clean_score_mNN`（配对列 `clean_score_qualified_mNN`）
- 附件4 投诉量 → `complaint_count_mNN`；附件5 → `problem_count_mNN`
- 附件6 实际利润 → `profit_actual_mNN`、定额 → `profit_quota_mNN`、合格标志 → `profit_qualified_mNN`
- 门店标识列「月份门店」→ `store_id`；总表/预处理等无别名源 → `unnamed_mNN`（144 行，待人工确认）

### 2) fillna_rule（附件4/5「空格处为0」）
- 附件4/5 共 26 行写入 `0（表标题声明：空格处即 0，非缺失）`，口径来源在 `intake_summary.md` 的「口径来源说明（fillna_rule）」段逐条列出表标题原文（如「附件5  公司发现各门店问题数量（空格处为0）」）。
- **有硬证据**：附件5 多个月份列缺失率报 100%（`T06/1.0`、`T06/4.0` 等），若按缺失处理会把「当天无问题（=0）」误删，这正是该口径存在的意义。缺失率列仍按原值统计，落库前须按 fillna_rule 处理（已在 summary 中写明）。

### 3) 口径冲突：12 条，全部可在原始数据核查
说明：队长提示「文档预期 7 条量级」，我 grep 了 `docx_dump.txt`：文档只给**结构性**要求（每条冲突含 4 个必需键、写入 caliber_conflict 表、`llm_tokens_in ≤ 2000`），**没有给出应检出的条数**；7 条不是验收阈值。我按真实结构检出 12 条，比预期多，原因是把「同一文件跨 sheet」和「分组表头丢失」两类真实问题也纳入统计。

每条冲突现含 4 个必需键：`conflict_type` / `field` / `evidence` / `suggested_caliber`（另保留 `type/field_base/variants/tables/sheets/message` 兼容字段）。evidence 逐条给出 `{table_id, source_file, sheet, field_raw, dtype, sample_values}`。

12 条按族归纳（每条证据都已在原始文件核对过）：
| # | 类型 | 字段 | 证据（源） | 为什么算冲突 |
|---|---|---|---|---|
| 1 | unit | `<表级单位:万元>` | 附件6 标题「…（万元）」，字段只有月份编号、无单位 | 单位只在表标题声明；附件1~5 为无量纲得分/合格/计数，跨表混算有量纲风险 |
| 2 | definition | `1..12`（12 字段聚合） | 总表.xlsx（float 分）、预处理/确定资格（数值）（int 0/1）、预处理/确定资格（str 合格/不合格） | 同名列在不同 sheet 承载**不同语义且类型不同** |
| 3 | definition | `1.0..12.0` | 附件1（float 得分）、附件4（int 投诉量）、附件6（str 含文本表头） | 同上，跨附件同名月份列语义/类型不一致 |
| 4 | definition | `1#2,2#2,3#2,4#2` | 预处理/合并表格（int 0/1）↔ 预处理/附件2（str 合格） | 同一概念的文本/数值双写 |
| 5 | definition | `5#2..11#2`（7 字段） | 同上 | 同上 |
| 6 | definition | `<分组表头:1.0..12.0>` | 附件2：`1.0`=float 得分、`1.0#2`=「合格」 | 列头是「月份」两级表头，二级语义（得分/合格）在解析中丢失 |
| 7 | definition | 同上 | 附件3（得分/合格） | 同上 |
| 8 | definition | 同上 | 附件6（定额/实际利润/合格，三元） | 同上，附件6 月份列有 3 个二级语义 |
| 9 | definition | `<分组表头:1..12>` | 预处理/附件2（float 96 ↔ str 合格） | 同上（中间表同样丢二级表头） |
| 10 | definition | 同上 | 预处理/附件3 | 同上 |
| 11 | definition | 同上 | 预处理/附件6 | 同上 |
| 12 | definition | `<分组表头:12>` | 预处理/Sheet4：`12`=int 0/1 ↔ `12#2`=str「A1/A2」 | 门店编号列被前向填充**错标为「12」**（列头错位） |

原始数据核对记录（可复核）
- `预处理-有资格月度.xlsx`：`确定资格`/`确定资格（文本）` 值为 合格/不合格（str），`确定资格（数值）`/`年度资格`/`Sheet4` 为 1/0（int）→ 真实双写，支撑 #2/#4/#5。
- `附件2`（原始 .xls）：第 0 行标题、第 1 行 `['月份门店',1,'',2,'',…]`、第 2 行 `['A1',96.0,'合格',96.3,'合格',…]` → 月份列成对（得分/合格），支撑 #6。
- `附件6`：第 2 行 `['A1','利润定额',38.0,'实际利润',42.1,'合格',…]` → 三元二级语义，支撑 #8 与 `profit_*` canonical。
- `预处理-有资格月度.xlsx`/`Sheet4`：第 0 行 `['','1',…,'12','']`、数据行末列为 `A1/A2` → 末列被填充为「12」，支撑 #12。

修了什么（相对第一批）
1. **跨 sheet 比较 bug（重要）**：原 definition 规则要求「跨不同 table_id」，导致同一文件内跨 sheet 的真实冲突（确定资格文本/数值）**全部漏检**；改为以 `(table_id, sheet_name)` 为比较单元。
2. **表级单位规则**：新增「单位只在表标题声明」检测，并用 `_looks_like_unit` 排除「（空格处为0）」这类说明性括号被误当单位（首版误报 3 条已修）。
3. **分组表头丢失规则**：新增；要求组内至少一个「文字型」语义，避免把纯 dtype 差异重复计数。
4. **聚合**：首版逐列成条产生 111 条噪声 → 现按「文件+dtype 组合」与「sheet+受影响列」聚合为 12 条，每条仍带代表性证据。
5. **4 必需键**：所有冲突补 `conflict_type/field/evidence/suggested_caliber`。

未做（诚实记录）
- 预处理/总表 里**无列头但内容是常量文本**的列（如 预处理/附件2 第 2 列整列值为「服务态度」）目前被 `field_raw==''` 跳过，未纳入体检与冲突（该 sheet 报 23 字段而非 24）。这是一处**已知漏检**，需在 `_field_records` 里为未命名列补 `<未命名列#idx>` 并加规则，本轮时间不足未做。
- `period` / `entity_scope` 两类在真实数据上**0 命中**（所有附件均为月度、范围为门店，本来就一致），我没有为了凑数放宽词表——放宽只会制造假冲突。

### 4) 顺带修掉 netguard-check 的误报（cli.py）
`netguard-check` 原先按文本正则扫描，把 Tess 的 `tests/test_fr5_guard_runner.py` 里**作为测试数据**出现的 `import requests` / `import socket` 字面量（docstring + 字典值）误报为违规，导致 exit=1。已改为 **AST 检测真实 import 语句**：
- 现在 `netguard-check` → 通过 exit=0；
- 反向验证：临时文件写 `import socket` 仍被检出（`artifacts\tmp\evil.py:1: 违规 import socket`），检测能力未削弱，只是排除了字符串/文档误报。

## P3 FR-5 头尾闭环 —— 状态：**机制 PASS，端到端未取得绿色结果（模型能力不足，如实记录）**

CHANGED_FILES（增量）
| 文件 | 说明 |
|---|---|
| `mmagent/agents/coder.py` | `generate_script(task_spec, prev_error=None, runner=None)`：RoleRunner(prompt_version=`coder@1.0.0`) → 取 code → `guard.validate_script_ast`；违规则带守卫报错以 temperature=0.0 重试 1 次，仍不过抛 `ScriptGenerationError`（不返回违规脚本） |
| `mmagent/agents/prompts/coder.system.j2` | 补成文档 5.3 schema：`module/filename/purpose/declared_outputs/code/figure_specs` + 硬约束与 xlrd 取值提示 |
| `mmagent/pipeline/repair.py` | 真实实现：`repair_once(prev_src, stderr_tail, regen_fn, max_rounds=1) -> str`；**pipeline 不 import agents**，regen_fn 由上层回调注入；纯逻辑＝拼错误上下文→调 regen_fn→guard 复检→返回；含 `build_error_context`（≤2000 字符）与 `RepairError` |
| `mmagent/tools/solve_tool.py` | 新增 `solve(task_spec, workdir, gen_fn, regen_fn, timeout_s, repair_rounds)`：generate → guard → run_script → 失败（守卫违规或 rc≠0）则 repair 一轮后重跑；`tools` 不 import agents，gen/regen 由上层注入 |
| `mmagent/agents/base.py` | `RoleRunner.run(..., temperature=None)` 可覆盖首档温度；`OllamaClient.chat(..., num_predict=None)` 支持 token 上限与 `think` 开关，并在 content 为空时兜底取 `thinking` |
| `config/models.yaml` | `timeout_s` 120→300（长脚本生成）；新增 `think: false`（见下方根因） |

### 离线链路实测（全绿）
- `repair_once`：注入返回违规源码的 regen → 抛 `RepairError`（含守卫违规行号）；注入「先违规后合规」序列 → 2 轮内修好并返回新源码。
- `solve`：①生成违规脚本（`import subprocess`）→ regen 修好 → 执行成功（`ok=True, repaired=True, produced_files=['out.txt']`）；②生成合规但 `raise SystemExit` → regen 修好 → 执行成功；③无 regen_fn 且违规 → 直接抛 `ScriptGuardError`（不静默）。
- 静态验证：`solve_tool.py` 的 AST 中无 `import agents`（依赖方向守住）。

### 真实 Ollama 端到端冒烟（5 轮，证据在 `artifacts/runs/smoke1..5/`）
任务：读 `data/附件1.xls`（.xls 旧格式）出「各门店月度满意度得分」折线图 + manifest.json。模型：`qwen3.5:latest`（本机 11434）。

| 轮次 | 生成 | 守卫 | 执行 | 修复轮 | 结果与关键发现 |
|---|---|---|---|---|---|
| smoke1 | OK（67 行） | 通过 | **未真正执行** | 修复调用失败 | 定位**我的 runner bug**：相对 `script_path` + `cwd=workdir` 导致路径二次拼接（`…smoke1\artifacts\runs\smoke1\solve.py`）→ 已修为先 `resolve()` |
| smoke2 | OK | 通过 | rc=1 | 修复成功但重跑仍 rc=1 | 脚本 bug：`'Cell' object has no attribute 'strip'`；修复轮报 `LLMCallError: Expecting value: char 0` |
| smoke3 | OK | 通过 | rc=1 | 同样空 JSON | 脚本 bug：以文本模式 `open('data/附件1.xls','r')` 喂 xlrd、`cell(row=,column=)` 传参错 |
| — | 探针复现根因 | — | — | — | **`content len=0 / thinking len=12098 / done_reason=length`** → qwen3.5 是思考型模型：思考开启时输出全在 `thinking`，`content` 恒空；且我的 `max_tokens` **从未传给 Ollama**（无 `num_predict`），故思考吃满长度上限。修复：`think:false` + `num_predict=max_tokens` + thinking 兜底 → 复测 `content len=81, thinking len=0, done_reason=stop` ✅ |
| smoke4 | OK | 通过 | rc=1 | 修复重跑 rc=1 | **闭环首次逐环全通**（生成→守卫→运行→repair→重跑）；脚本 bug：文件名写成 `data/附件 1.xls`（多空格） |
| smoke5 | OK | 通过 | rc=1 | 修复重跑 rc=1 | 加「路径逐字符照抄」约束后，模型**仍**写成 `data/附件 1.xls` → 同一 quirk 稳定复现 |

结论（不粉饰）
- **机制 PASS**：guard / runner / repair / solve 四环均被真实触发并验证；`think` 与 `num_predict` 两个缺陷是本轮最有价值的修复（此前所有 JSON 模式调用在长输出下都会失败）。
- **端到端未取得绿色结果**：`qwen3.5:latest` 连续 3 次生成的脚本都不可运行（xlrd `Cell` API 误用、文本模式读 .xls、中文文件名多空格），修复轮也未能纠正。故本任务下的**首次可运行率本轮实测 0/3**（不含 smoke1 的 runner 自身 bug 轮次）。
- 这不是 guard/runner 的问题：守卫正确放行了合法脚本、runner 正确回传了 stderr（修复上下文完整），失败根因在模型输出质量。

给后续的改进建议（未实施，避免为过测试而粉饰）
1. 输入文件名改 ASCII（如 `att1.xls`），消除 CJK 空格 quirk；
2. 把 xlrd 取值/路径写入做成 few-shot 模板注入 prompt（比纯文字约束更有效）；
3. 修复轮把上一版**完整源码**一并回传给模型（当前只传 stderr），并按「仅输出修改后的 code」约束，减少重犯；
4. 建议把「首次可运行率」基线按 0/3 记入 `docs/BASELINE_REPORT`，不要沿用规划书 ≥70% 的假设值。

### 本轮新增/发现的缺陷修复清单（均为真实缺陷）
1. `runner.run_script` 相对路径二次拼接 → 脚本根本没执行（严重，影响 FR-5 全部执行链）。
2. `OllamaClient` 未传 `num_predict` + 未处理思考型模型 `thinking` 字段 → JSON 模式在长输出下必然解析失败（严重，影响 agents 全链路）。
3. `netguard-check` 文本正则误报测试数据（见 P2 §4）。
4. `config/models.yaml` 超时 120s 对长代码生成偏短 → 300s。





---

## 修复轮 3（FR-2 精确规格 A~H + P0 触发器 + 4 条源码缺陷）

> 时间口径以队长为准（真实时间 19:53→20:3x）。本轮的 intake.py 为**整文件重写**，
> 不存在 `_period_of`（门店列 period 在 `_add_field` 后强制置空）。

### 1) 修复项与自检输出

**A — 末列丢失（merged_cells 分组归属）**
- 弃用 `last_non_empty` 边界；月组右界改「下一个月起始列」。附件6 每月合并区常只盖 2 列，
  第 3 列（评级）不在合并内 → 延伸后完整召回。
- 自检 `run_intake_audit(dataset,'artifacts/intake_v4')`：
  附件1=13、附件2=**25**、附件3=**25**、附件4=13、附件5=13、附件6=**37**；附件1 有效行=**157**（物理160/空3）。

**B — 附件6 三层复合表头 + dtype**
- raw row2（利润定额/实际利润/空）识别为子表头并展开为『N月.利润定额』『N月.实际利润』『N月.评级』。
- dtype：定额=int、实际利润=float、评级=str（不再全 str）；门店=157。
- 表级数值合计（`numeric_sum`）：**利润定额=27289.0**、**实际利润=31344.0**；附件4=**432.0**、附件5=**131.0**。

**C — 语义化命名 / table_id / canonical**
- table_id 用文档口径 `summary_master/att1_satisfaction..att6_profit`；canonical base
  `satisfaction_overall/service_score/clean_score/complaint_count/problem_count/profit`；
  逐月 canonical `*_mNN`，附件6 `profit_quota_mNN/profit_actual_mNN/profit_qualified_mNN`；
  spotcheck canonical（satisfaction_overall/complaint_count/profit_actual）已打通；原列名保留 `field_raw`。

**D — 口径冲突检出（5 条，证据行齐备）**
- C006[definition] 评级 在附件2/3/6 三套考核定义（同名不同义）①；
- C004[definition] 得分 跨附件 dtype float/str，附件3 的 17×`'97.3.4'` 文本混入 ②；
- C002[definition] N月 在 投诉量/问题数量/满意度/全量汇总 同名不同义（附件4 vs 附件5）③；
- C003/C007[unit] N月单位、评级单位冲突。已把 int/float 视为同粗类型，避免等价差异误报。

**E — 「空格处为0」** 附件4/5 数值列 `blank_as_zero=true`、`fillna_rule` 落 csv/summary，missing_rate 记 0。
**F — row_count 三口径** 每表 `physical_rows/effective_rows/blank_rows`（附件1=160/157/3、附件6=157/157/0）。
**G — 留痕清单** `unmapped_columns`（附件1 的 N 列 `derived=true`）、`dirty_cells`（17 处 `'97.3.4'`，附件3 列 T/V/X，带 sheet+excel_row+原值，不改值）。
**H — 输入白名单** `config/intake_scope.yaml`；`skipped_files=['预处理-有资格月度.xlsx','预处理2-排名及其获奖名单.xlsx']`。

### 2) P0 触发器修复
- `schema.sql` 增 `trg_profile_adopted_guard_insert`（BEFORE INSERT，主键已存在且 adopted → RAISE ABORT），
  堵住 `INSERT OR REPLACE` 隐式 DELETE 绕过（`recursive_triggers` 默认 OFF）。
- `db.py connect()` 增 `PRAGMA recursive_triggers=ON`。
- 自检：重跑 `db init data/mm.db` 幂等 → `sqlite_master` 命中触发器；`journal_mode=wal`、
  `recursive_triggers=1`；对已 adopted 行 `INSERT OR REPLACE` → **ABORT**；正常写入 `MAX+1` → OK。

### 3) 四条源码缺陷
1. `guard.py` 改 **import 白名单**（pandas/numpy/matplotlib/math/json/csv/sqlite3/pathlib/
   statistics/itertools/collections/datetime/re/random）+ 危险调用黑名单（补 httpx/urllib3/http.client）。
   自检：`from os import system`、`importlib.import_module("subprocess")`、`import httpx`、`import socket` 全 BLOCK；pandas+matplotlib 脚本 PASS。
2. `db.py`：`journal_mode=WAL` 从 connect() 移到建库路径 init_db()，connect 只留 foreign_keys/busy_timeout/recursive_triggers。
3. `sanity.py`：`_check_range` 越界判定先剔 NaN → `[50,NaN,120]` 越界 rows=`[2]`。
4. `intake`：门店列 period 恒空（field_mapping store_id 行 period=''，9 行一致）。

### 4) 口径裁定（队长）
`read_profile(profile_id,'last')` 口径**已由队长裁定**：优先返回 version_id 最大的 `adopted`；
若无 adopted 则返回 version_id 最大的 `draft`；返回值必须带 `status`。已按此实现/核对。

### 5) 冻结 revision（磁盘 md5，供 Tess 基线）
```
intake.py  4a20fb0a41f8e751fa16c4194a10dbea
schema.sql dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py   7bf8d6bf77b805594a2c1cb1b1de52a6
db.py      084cfc517f1e2cce456b307a3f5fcee0
sanity.py  e25b0d7716a209cfbcf159b155c524d9
```

### 6) 未完成 / 已知风险（交接 Tess）
- **P3 端到端仍 0/3**（真实调 Ollama 生成的脚本连续不可运行：xlrd `Cell` API 误用、文本模式读 .xls、
  中文文件名多空格），按队长指示**不再调优 prompt**；机制四环（guard/runner/repair/solve）真实跑通。
- **P4 FR-9 字段级溯源取消**。
- 附件1 的 N 列派生判定阈值 0.05，属启发式；`_looks_like_sub_header` 依据「非空去重 ≤4 且非数字短文本」，
  数据首行不会误判（实测），但极端表仍可能误触。
- dirty 列判定要求「数值型文本占比 ≥50%」，附件3 的 10/11/12 月列满足；若某列脏值占比过低可能漏记。

---

## 修复轮 3.1（队长复核后补两项）

1. **`skipped_files` 落盘**：`schema_report.json` 顶层新增 `skipped_files`（list[{file, reason}]），
   并单独写 `skipped_files.json`；`intake_summary.md` 同步注明排除原因。
   内容：`预处理-有资格月度.xlsx`、`预处理2-排名及其获奖名单.xlsx`，
   原因＝「不在 FR-2 黄金输入白名单内（config/intake_scope.yaml），属预处理参考材料」。
2. **低基数文本列 `value_counts`**：字段字典新增 `value_counts`（distinct ≤ 50 或门店列时填充）。
   交叉验证（对齐 Vera 独立真值）：附件2 `12月.评级`={合格:144,不合格:13}、
   附件3 `12月.评级`={合格:154,不合格:3} ✓（此前只有 5 个 samples，Tess 无法断言计数）。

**冻结产物目录**：`artifacts/intake_v5/`（该目录 grep `skipped_files` 命中 schema_report.json + skipped_files.json）。

**更新后冻结 revision（md5）**
```
intake.py  2c71fba7a8bb4f216e9fa8d82364546f   ← 本轮更新（skipped_files + value_counts）
schema.sql dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py   7bf8d6bf77b805594a2c1cb1b1de52a6
db.py      084cfc517f1e2cce456b307a3f5fcee0
sanity.py  e25b0d7716a209cfbcf159b155c524d9
```
其余字段数/冲突数不变（153 / 5）。

---

## 附：before / after 关键指标对照（第一批 → 最终）

| 模块 / 指标 | 第一批（基线） | 最终 | 驱动原因（哪次核验逼出来的） |
|---|---|---|---|
| intake 总字段数 | ≈160（表头探测期，分表大量丢列/错并；曾出现「各表仅 1 字段」） | **153**（8 sheet） | A 末列召回 + 单列月角色收敛 + 未映射列外移 |
| 口径冲突数 | 0 | **5** | D：0 条是规则失效（`re.fullmatch(r"[\d.\-]+")` 误杀 + 缺三类规则）；修后检出「评级三套定义 / 得分 dtype 退化 / 附件4vs5 同名不同义」 |
| 附件2 / 附件3 字段 | 24 / 24 | **25 / 25** | A：`last_non_empty` 边界永久丢弃末列 → 改 merged_cells 分组归属 |
| 附件6 字段 / dtype | 35 / 全 str | **37** / int+float+str 三类 | B：三层复合表头未展开（raw row2 被当数据行）→ 展开子表头；月组右界延伸到下一月起 |
| 附件1 行数 | row_count=160（单口径） | phys/eff/blank = **160/157/3** | F：空分区行拆解（排除 row7/128/151） |
| 附件6 数值合计 | 无载体 | 定额=**27289**、实际利润=**31344**（和=58633） | B 修复 dtype 后数值列可求和；队长补 `numeric_sum` 载体 |
| db `connect()` 时延 | Tess 实测 ~385ms/次（本机 WAL 路径复测 168ms） | **~10ms/次** | Tess 性能缺陷：`journal_mode=WAL` 是**库级持久设置**，从 connect() 下移到 init_db() |
| guard 策略 | 黑名单（`from os import system` 可绕过） | **import 白名单** | Tess 安全缺陷：黑名单无法穷举绕过路径 |
| sanity NaN | NaN 被并入越界行号 | 先剔 NaN | Tess 误报缺陷：`[50,NaN,120]` 越界行 →`[2]` |
| adopted 不可变 | 仅 UPDATE/DELETE 触发器 | 增 `trg_profile_adopted_guard_insert` | 队长 P0：`INSERT OR REPLACE` 隐式 DELETE 拼过 BEFORE DELETE（recursive_triggers 默认 OFF） |

**约定产物目录**：`artifacts/intake/`（含 `schema_report.json` 顶层 `skipped_files` + 独立 `skipped_files.json`、
字段级 `value_counts`、`numeric_sum`、三口径行数）。探针目录 intake_probe/v2/v3/v4/v5 已清理。

---

## 修复轮 3.2（队长最后 3 条小修，收工）

1. **附件5 1月/4月 整列全空按 0 计**：`_add_field` 新增列级标记
   `all_blank_as_zero`（表标题声明「空格处为0」且该列全空 → true），同时
   `missing_rate=0.0`、`blank_as_zero=true`，不再以「缺失率 100%」流到下游。
   自检：附件5 `1月`/`4月` → missing_rate=0.0、blank_as_zero=True、all_blank_as_zero=True；
   其余数值列不受影响（如 `2月` all_blank_as_zero=False，blank_as_zero=True）。
2. **table_id 对齐文档口径**：附件6 `att6_profit` → **`att6_profit_quota`**
   （`config/field_aliases.yaml` 增 `legacy_table_ids: {att6_profit: att6_profit_quota}` 兼容映射；README §8.7 说明）。
3. **entity_scope**：核查当前产物 `entity_scope ∈ {门店, ''}`（门店 126 / 空 27），
   **不存在单字 `店`**——队长看到的是修复前快照；源码与 config 均无 `'店'`（grep 0 命中），无需改动。

约定产物目录 `artifacts/intake/` 已重新生成：表数=8 字段数=153 冲突数=5；
附件6 定额合计=**27289.0**、实际利润=**31344.0**（与 Vera 真值一致）。

**最终冻结 revision（md5）**
```
intake.py                f94571cce0a5e9ecbb6e31138a4a4ef4   ← 本轮更新（all_blank_as_zero）
config/field_aliases.yaml c4ea321af22aaab244f88ee7dc08646e   ← 本轮更新（att6_profit_quota + legacy 映射）
README.md                9ebd5b13e59e0b987139dca7a1b1fed6
schema.sql               dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py                 7bf8d6bf77b805594a2c1cb1b1de52a6
db.py                    084cfc517f1e2cce456b307a3f5fcee0
sanity.py                e25b0d7716a209cfbcf159b155c524d9
config/intake_scope.yaml 72fff50745f8805a6c1a5a80ce2750e9
```
import 冒烟：39 模块全通过。**源码就此冻结，停止改动。**

---

## 修复轮 3.3（队长撤回 3.2 的第 2/3 条，仅保留附件5 修复）

- **保留并完成（第 1 条）**：附件5 `1月`/`4月` 整列全空 → 列级 `all_blank_as_zero=true`、
  `missing_rate=0.0`、`blank_as_zero=true`；`intake_summary.md` 的缺失率 Top10 改用
  **post-fillna 口径**排序（这两列不再占前两名），并新增「整列全空按 0 计」清单。
- **撤回（第 2 条）**：`att6_profit` → `att6_profit_quota` **已回退**，恢复文档口径 `att6_profit`
  （Vera 核对 docx 原文无 `_quota` 后缀）；`legacy_table_ids` 与 README §8.7 相应删除/回退。
- **撤回（第 3 条）**：`entity_scope` 无需改动（本就为「门店」，无单字写法）。

**最终冻结 revision（md5）**
```
intake.py                 <见回传消息>
config/field_aliases.yaml <见回传消息>
README.md                 <见回传消息>
schema.sql                dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py                  7bf8d6bf77b805594a2c1cb1b1de52a6
db.py                     084cfc517f1e2cce456b307a3f5fcee0
sanity.py                 e25b0d7716a209cfbcf159b155c524d9
config/intake_scope.yaml  72fff50745f8805a6c1a5a80ce2750e9
```
产物 `artifacts/intake` 已重新生成（8 表 / 153 字段 / 5 冲突）。**源码冻结，停止改动。**

---

## 修复轮 3.4（table_id 按 docx 原文对齐，收工）

以 `docx_dump.txt` 穷举 grep 为准（原文：`att1_satisfaction`×4、`att5_complaint`×1、`att6_profit`×3）：
- 附件6 保持 **`att6_profit`**（无 `_quota` 后缀）；附件5 → **`att5_complaint`**。
- `config/field_aliases.yaml` 增 `legacy_table_ids: {att6_profit_quota: att6_profit, att5_problem: att5_complaint}`。
- README §8.7：文档仅显式定义 `att1_satisfaction`/`att5_complaint`/`att6_profit`；
  `att2_service`/`att3_clean`/`att4_complaint` 为按同构规则外推。
- `entity_scope` 保持现状（门店×126/空×27，无单字）。

产物 table_ids：summary_master, att1_satisfaction, att2_service, att3_clean,
att4_complaint, **att5_complaint**, **att6_profit**。`artifacts/intake` 已重生成（8 表/153 字段/5 冲突）。

**最终冻结 revision（md5）**
```
intake.py                b16d96b97e353abd151f594ad6571f9f
config/field_aliases.yaml ecbd5d8ab4fa0e402255028bcefdc3ca
README.md                2175bb71faab5b4618373e8ca618fc92
schema.sql               dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py                 7bf8d6bf77b805594a2c1cb1b1de52a6
db.py                    084cfc517f1e2cce456b307a3f5fcee0
sanity.py                e25b0d7716a209cfbcf159b155c524d9
config/intake_scope.yaml 72fff50745f8805a6c1a5a80ce2750e9
```
import 冒烟 39 模块全通过。**源码冻结，停止改动。**

---

## 修复轮 3.5（对齐队长冻结基线：撤回 3.4 的 att5 改名）

队长最终决定**不追** att5 文档口径（文档仅 1 处提及，改名会打破即将冻结的 md5 基线）。
由于我的 3.4 已先把 `att5_problem→att5_complaint` 落地，本轮**已回退**，恢复队长冻结态：
- `config/field_aliases.yaml`：附件5 恢复 `att5_problem`，删除 `legacy_table_ids`，附件6 保持 `att6_profit`
- `README.md` §8.7 回退为一行「附件6 即 att6_profit」
- 产物 table_ids 恢复：att5_problem、att6_profit（att5_complaint 口径与 att2/3/4 外推命名统一列入「已知差异/续作项」）

**冻结 md5（与队长复核值逐字符一致）**
```
intake.py                b16d96b97e353abd151f594ad6571f9f
config/field_aliases.yaml 680507e04d8520e92470630995d500e0
README.md                321e1d598fd42d6d998d5e53e8b95bb5
schema.sql               dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py                 7bf8d6bf77b805594a2c1cb1b1de52a6
db.py                    084cfc517f1e2cce456b307a3f5fcee0
sanity.py                e25b0d7716a209cfbcf159b155c524d9
config/intake_scope.yaml 72fff50745f8805a6c1a5a80ce2750e9
```
产物 `artifacts/intake`：8 表 / 153 字段 / 5 冲突。**源码冻结，任务结束。**

---

## 修复轮 3.6（最终定案：恢复 3.4 的文档口径命名，与队长确认基线一致）

时序交叉说明：3.5 曾按"不追 att5"回退；随后队长**确认 3.4 的改名正确**
（`att5_complaint` / `att6_profit` 均为 docx 文档口径，legacy 映射方向正确），
并已以此 md5 同步 Tess。故 **3.5 的回退被 3.6 取代**，恢复到 3.4 状态：

- `config/field_aliases.yaml`：附件5 = **`att5_complaint`**、附件6 = **`att6_profit`**；
  `legacy_table_ids: {att6_profit_quota: att6_profit, att5_problem: att5_complaint}`
- `README.md` §8.7：文档显式定义 att1_satisfaction / att5_complaint / att6_profit，att2/3/4 为同构外推
- 产物 `artifacts/intake`：8 表 / 153 字段 / 5 冲突；table_ids 含 **att5_complaint**、**att6_profit**

**最终冻结 md5（= 队长复核值 = Tess 基线）**
```
intake.py                 b16d96b97e353abd151f594ad6571f9f
config/field_aliases.yaml ecbd5d8ab4fa0e402255028bcefdc3ca
README.md                 2175bb71faab5b4618373e8ca618fc92
schema.sql                dd65844bc5d0815d6c377c2b7c4dbfd4
guard.py                  7bf8d6bf77b805594a2c1cb1b1de52a6
db.py                     084cfc517f1e2cce456b307a3f5fcee0
sanity.py                 e25b0d7716a209cfbcf159b155c524d9
config/intake_scope.yaml  72fff50745f8805a6c1a5a80ce2750e9
```
**源码冻结，任务结束。**
