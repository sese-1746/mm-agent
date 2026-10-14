# 需求 → 实现 覆盖矩阵（场景一 数模竞赛协作 Agent）

- 调查员：Vera（只读，未改 mmagent/ 任何文件）
- 代码快照时间：**2026-10-13 18:58**（实现工程师 Max 的任务 #2 当时仍为 in_progress，本矩阵是"进行中"快照，非终态）
- 输入文档：
  - `C:\Users\Lenovo\Desktop\agent项目\场景一-数模竞赛协作Agent-实施规划书.docx`（V1.1，204 段 / 19 表）
  - `C:\Users\Lenovo\Desktop\agent项目\何昆阳_场景一-数模竞赛协作Agent-项目开发计划书.docx`（V1.1，83 段 / 17 表）
  - `H:\mm-agent\.agent-runs\2026-10-13\task-001\plan.md`（2 小时冲刺范围）
- 解析产物：`docx_dump.txt`（两份 docx 全文，含表格）、`probe_docx.py`（可复跑）
  - 注：该解释器原本**未装** python-docx，已 `pip install --user python-docx`（1.2.0）后解析成功——如需复核请直接用此解释器。

**需求总量**：功能需求 FR-1~FR-13 中 FR-11 已并入 FR-4，**实际 12 条**；非功能需求 NFR-1~NFR-10，**10 条**；合计 22 条。
**P0 五条**：FR-2、FR-5、FR-6、FR-9、FR-13（+ FR-8 最小形态随阶段一一并落地，不计入 P0 条数）。

**覆盖计数（快照时刻）**：22 条中 **已实现 0 条**、**部分实现 9 条**、**未实现 13 条**；P0 五条全部为"部分实现"，按文档纪律（未对应测试用例的 P0 不得关闭）**目前 0/5 可关闭**。

---

## 表 1 需求覆盖矩阵

| 需求编号 | 需求摘要 | 验收要点（摘自规划书） | 状态 | 证据文件 | 缺口说明 |
|---|---|---|---|---|---|
| FR-1（P1 薄片） | 题面解析与骨架（5 min 骨架 + 30 min 全量档案） | `artifacts/parse/subproblems.json`、`SKELETON.md`；`tests/test_fr1_parse.py`；固定题集齐全率 ≥80% | **未实现** | `mmagent/agents/archivist.py`（仅 `make_runner` 骨架）、`agents/prompts/archivist.system.j2` | 无题面 docx→JSON 解析逻辑；schema 只有 `{"required":["summary"]}`，与文档规定的 subproblems/deliverables/constraints/scoring_points/open_questions 五键 schema 完全不符 |
| FR-2（P0） | 附件与数据体检（确定性） | `run_intake_audit(dataset_dir,out_dir)` 一次走完 6 张附件，产出 4 文件非空；每表每字段含 table_id/sheet_name/field_raw/field_norm/dtype/row_count/missing_rate/merge_cells/sample_values[]；冲突 4 类；LLM 解释 token ≤2k | **部分实现** | `mmagent/pipeline/intake.py:227`、`:95`、`:147`；产物 `artifacts/intake/{schema_report.json,caliber_conflicts.json,field_mapping.csv,intake_summary.md}` 均在且非空（实测 19 表 / 161 字段）；`mmagent/tools/intake_tool.py`；`cli.py:89 cmd_intake` | **① 表头只取第 0 行**（`intake.py:70` `sh.row(0)`），而附件1~6 第 0 行是合并大标题 → 实测附件1~6 **每张只识别出 1 个字段**（field_count=1），"月份/门店"宽表结构完全丢失；② 因此 `field_raw/field_norm/dtype/unit` 对该 6 表无意义，口径冲突实测 **0 条**（文档示例为 7 条）；③ 文档要求的抽检字段 `satisfaction_overall / complaint_count / profit_actual` 在产物中不存在，`spotcheck` 无法执行；④ `intake_llm.explain_conflicts` 为模板降级（未接 LLM，token ≤2k 约束未验证）；⑤ 无 `cli spotcheck` |
| FR-3（P1 薄片） | 方案 Agent + 4 类模型卡片（AHP、熵权+TOPSIS、多元回归、线性规划） | ≥3 候选 + data_check 全条；明显错配数 0；`tests/test_fr3_planner.py` | **未实现** | `mmagent/agents/planner.py`（骨架 schema 仅 `{"required":["tasks"]}`） | 无 `templates/cards/{ahp,entropy_topsis,mlr,linprog}.json`；planner 输出 schema 与文档的 candidates/data_check/recommended_top2 三键结构不符；无"data_check 与 field_mapping 逐条对照 + satisfied=false 拦截"的硬校验 |
| FR-4（P2） | 检索与复盘模板（含原 FR-11） | `data/chroma/`、`source_ledger` 表；引用必须带 card_id/source_url；混合检索+精排+压缩 | **未实现** | `mmagent/rag/adapter.py`（占位，`search()` 直接 `raise NotImplementedError`）；README 明确"当前占位未启用" | 无 ChromaDB/BM25(jieba)/RRF(k=60)/交叉编码器精排/MMR/句子压缩任何代码；无 `source_ledger` 表；`<RAG_ROOT>` 待填项未回填；无 `mmagent/rag/vendor_path.py` |
| FR-5（P0） | 代码生成/求解/一键出图 闭环 | POST /api/v1/solve/run 走「生成→落盘→guard→执行→sanity→出图→写映射」；guard 禁 os.system/subprocess/eval/exec/网络导入/绝对路径写盘；runner 捕获 returncode + declared_outputs 比对；sanity blocking 阻断；repair 只允许 1 轮；≥50 次回归 first_run_rate ≥70%、sanity_pass ≥90% | **部分实现** | `pipeline/guard.py:38 validate_script_ast`（禁 import/call/attr-call 已实现）、`pipeline/runner.py:17 run_script`（超时/returncode/manifest 比对/产物兜底扫描已实现）、`checks/sanity.py:56 run_sanity_checks`（得分 0~100、占比 0~1、利润≥0 内置 + range/not_null/unique/row_count_match 自定义，返回 blocking）、`tools/solve_tool.py`、`api/routes_solve.py` | **① 无"生成"环节**：coder 仅 `make_runner` 骨架，无 `generate_script`；② `repair.py:repair_once` 直接抛 NotImplementedError（未接 LLM，1 轮修复闭环不存在）；③ guard 缺"禁绝对路径写盘"检查；④ runner 缺 stdout/stderr **落盘归档**（只在内存截尾 4000 字符）、缺 `run_id`/`elapsed_ms`/`outputs[{path,bytes,exists}]` 出参结构；⑤ sanity 出参是 `passed/violations/blocking`，文档要求 `blocking/checks[{name,table,passed,detail}]/failures[{name,row_ref,actual,expected}]`；⑥ 无 `checks/units.py`（量纲）、无 `checks/cardinality.py`（行数/主键独立实现，现塞在 sanity 里）；⑦ 无 `tests/regression/a_case_set.yaml`、无 `cli regression`、无 ≥50 次回归与 first_run_rate 统计 |
| FR-6（P0） | 结果可视化（300 dpi、图题编号一致） | `render_figures(...)->FigureManifest`；统一 `apply_cn_style()`；每图中文标题+单位+图例+300dpi；`fig_{chapter}{seq}{slug}.png`；`figure_manifest.csv` 九列；一键重出全部图 ≤5 min；图题与正文编号一致率 100%（`checks/numbering.py::check_figure_numbering`） | **部分实现** | `mmagent/plotstyle.py`（`apply_paper_style` rcParams 中文字体/unicode_minus/dpi300/bbox_tight、`available_cjk_font()` 检测）、`pipeline/render.py:43 render_figures`（line/bar/scatter/hist 四型、dpi 元数据校验、缺字形告警、写 manifest）、`tools/plot_tool.py` | ① 函数名/风格函数名与文档不一致（`apply_paper_style` vs `apply_cn_style`，无 `re_plot_all`）；② manifest 只有 5 列（figure_id/path/kind/source_table/dpi），缺 title/chapter/seq/caption/run_id/version_id；③ 图文件名非 `fig_{chapter}{seq}{slug}.png`；④ 无 `checks/numbering.py`、无编号一致率校验；⑤ 无 `templates/figures/*.json` 6 类模板；⑥ 无 `cli re-plot`、无 `fr6_replot_time.json` 耗时断言 |
| FR-7（P1 薄片） | 论文模板装配（五章骨架 + 图表位 + 附录代码位 + 页脚声明） | `pipeline/assemble.py::assemble_docx`；`artifacts/paper/骨架_v1.docx`；`checks/numbering.py` 通过 | **未实现** | 无 `pipeline/assemble.py`；仅 `agents/assembler.py` 骨架 + `prompts/assembler.system.j2` | 无 `templates/paper/*.docx`、无 python-docx 装配逻辑、无符号表/图表位/附录位/页脚"AI 辅助生成+审核人" |
| FR-8（P2 最小形态） | 进度守卫（倒计时 + 不做清单 + 无产出预警，零 LLM 成本） | `pipeline/watchdog.py::tick(now)`；`event_log` 中 action='alert'；`tests/test_fr8_watchdog.py` | **未实现** | `agents/coordinator.py` 有 `Stage.HALTED` 与 `HumanGateRequired`，但无任何时间维度预警 | 无 `watchdog.py`、无 `config/time_budget.yaml`、无「连续 6 h 无产出 / 超人时预算 20%」判定、无「最短可交版本」清单生成 |
| FR-9（P0） | 精简版赛题档案（单一事实源） | `write_version` 每次新 version_id；draft 保留最近 5 版（超限裁剪并写事件表）；adopted 不可覆盖/删除，回滚只能 `adopt_new_version`；字段级 producer/time/version_id/upstream_refs 可追溯；`POST /fields/{field}/review` 置待复核 | **部分实现** | `store/schema.sql:5 profile_doc` + 两个 BEFORE UPDATE/DELETE 触发器（`trg_profile_adopted_immutable` / `trg_profile_adopted_nodelete`）、`store/profile.py:55 write_version`（BEGIN IMMEDIATE + MAX+1 + 重试 3 次退避 100ms + 草案 LIMIT 5 裁剪）、`:103 adopt_new_version`（只 INSERT、supersedes）、`:26 AdoptedVersionImmutableError` + 事件表 `ADOPTED_IMMUTABLE`、`store/db.py` PRAGMA(WAL/foreign_keys/busy_timeout)、`tools/profile_tool.py`、`api/routes_profile.py` | ① 缺 `profile_field_trace` 表 → **字段级四要素追溯无法实现**（现在只有版本级 actor/created_at/upstream_refs）；② 无 `review_flag` 列与 `mark_review` 接口（文档 DoD 明确要求）；③ 无 `discard`（status 枚举用 `superseded` 代替 `discarded`，与 DDL 文档不一致）、无 `history` 查询、无 `adopt/discard/write` 路由；④ `read_profile` 不返回 `trace` 与 `token_estimate`；⑤ 无 `profiles/A2024.yaml` 剖面；⑥ 无 `tests/test_fr9_profile.py`——"写 8 次草案恰好 5 版 + adopted 覆盖抛异常"两条关键断言未验证 |
| FR-10（P1 薄片） | 一致性校验（正文数字 vs 结果表、重放报告） | `checks/consistency.py::compare_numbers(docx, results)`；`artifacts/reports/consistency.json` | **未实现** | 无该文件 | 无段落/单元格级数字比对、无差异清单 |
| FR-12（P1） | 一键导出打包 | `artifacts/export/A2024_pack.zip` 含 `export_manifest.csv`；缺声明或未处理报警即 422 阻断 | **未实现** | 无 `cli export`、无 `api/routes_artifact.py` | 无打包、无导出前合规阻断断言、无 operator 留痕 |
| FR-13（P0） | 合规声明 + 可追溯四字段 | `make_ai_disclosure(profile_id,version_id,out_dir)` 产出 `artifacts/compliance/ai_disclosure.{md,docx}`，含 Agent 参与范围/产物清单/人改比例（`compliance/edit_ratio.py`：n_manual_edits / n_total_paragraphs）/禁用声明；artifact 四字段 producer/produced_at/version_id/upstream_refs 抽检 20 个完整率 100% | **部分实现** | `mmagent/compliance/disclosure.py:57 make_ai_disclosure`（md+docx 双份、四节结构、模型标签/提示词版本从 artifacts+metric_obs 回查、禁云声明已写死）、`tools/compliance_tool.py`、`api/routes_compliance.py` | ① **`artifacts` 表缺 `upstream_refs` 列**（`schema.sql:44`），四字段之一结构性缺失 → 100% 完整率不可能达成；② 人改比例口径不符：文档要求 `edit_ratio.py` 按"人工编辑段落数 / 总段落数"统计 docx，实现改为 events 表 `actor LIKE 'human%'` 计数，且无数据时默认 0.3（文档禁止猜测值）；③ 无产物清单（artifacts 全量枚举）渲染进声明；④ 无 `check_traceability(sample,min_completeness)` 与 `/compliance/audit` 路由、无 `cli audit`；⑤ 无档案 draft/adopted 两级撤销语义（`POST /profile/{id}/discard`）配套；⑥ 无 `tests/test_fr13_disclosure.py` |
| NFR-1 | 推理性能：llm_latency_p95_ms ≤60000 | `metric_obs` 记录 + `artifacts/reports/latency.json` + `cli report --metric` | **部分实现** | `agents/base.py:102 _record` 写 `metric_obs(llm_latency, ms)`；`schema.sql:58 metric_obs` | 无 p95 聚合（`metrics.py::aggregate` 不存在）、无 latency.json 报告、无 `cli report`、无阈值退出码；且**角色调用链一次都未真实执行过**（无 LLM 端到端记录） |
| NFR-2 | 资源与成本：peak_rss_mb ≤8192、云支出 0 | `memory_peak.json`、`net_audit` 表、`cli report --metric peak_rss_mb` | **部分实现** | `cli.py:122 cmd_netguard_check`（静态扫描 requests/socket/urllib/ftplib，实测可用）；`cli.py:99 cmd_env_attest` 记录依赖与 Ollama 模型 | 无 `netguard.py` 运行期 socket 审计钩子（文档要求 `install_audit_hook()` + OutboundBlocked）、无 `net_audit` 表、无 pktmon 抓包核验脚本、无内存峰值采集 |
| NFR-3 | 时间约束：赛历锚点、倒计时 | `config/time_budget.yaml`、倒计时接口、`tests/test_nfr3_budget.py` | **未实现** | 无 | config 目录下只有 `models.yaml` |
| NFR-4 | 准确性：first_run_rate ≥0.70、sanity ≥0.90、端到端 ≤15 min、口径误差 0 | A 题回归集 ≥50 次 + 统计命令 | **未实现** | 无 `tests/`（快照时 `tests/` 下只有 `conftest.py`，1946 B） | 无 `a_case_set.yaml`、无 `a_regression.jsonl`、无 `cli regression`、无 spotcheck 真值 |
| NFR-5 | 可靠性：崩溃恢复丢失版本 ≤1、每 30 min 备份 | `event_log` 重建 + `backup_log` + `test_nfr5_recovery.py` | **部分实现** | `schema.sql:34 events` 表 + `store/db.py` WAL；profile 版本链可重建状态 | 无 `pipeline/backup.py`（wal_checkpoint + 快照 + U 盘镜像）、无 `backup_log`、无定时任务、无恢复演练用例 |
| NFR-6 | 可用性：新用户 ≤5 步 / ≤15 min，3 人 3/3 通过；7 个本地静态演示页 | `tests/usability/usability_log.csv` + 截图 + `cli usability check`；`frontend/` 7 页 | **未实现** | 无 `frontend/`、无 `tests/usability/` | 7 个演示页面（附件体检/代码与出图/赛题档案/资料库检索/进度与最短可交/合规可追溯/一键导出）全部缺失；无可用性记录机制 |
| NFR-7 | 安全与隐私：5 类正则脱敏（手机/学号/身份证/姓名列/导师），检出 20/20 | `security/desensitize.py::scan_and_mask` + `config/desensitize.yaml` + `desensitize_hit` 表；命中即阻断入库 | **未实现** | 无该模块 | 合规红线项，且是"入库前逐单元格替换 [已脱敏]"的强制关卡，当前完全空缺 |
| NFR-8 | 可维护性：核心 Python 源码 ≤3000 行；ruff/pytest 配置 | `cli loc count --scope core --max 3000` + `artifacts/reports/loc.json` + `tests/test_nfr8_loc.py` | **部分实现** | 实测 mmagent/**.py + schema.sql + prompts 共 **1977 行**（含注释/docstring；`mmagent` 包内 27 个 py/sql/j2 文件） | 无 `cli loc count` 门禁与报告、无 `pyproject.toml`（ruff/pytest 配置）、无 `requirements-dev.txt`；行数目前**合规但已用掉 66% 预算** |
| NFR-9 | 并发与队列：LLM/solve 串行化，depth>2 返回 429 QUEUE_FULL | `task_queue` 表 + 串行执行器 + `/api/v1/queue/status` + `data/.inflight.lock` | **未实现** | 无 | 无队列表、无优先级（llm=5/solve=9）、无 429 错误码、无互斥文件锁；`app.py:50` 全局异常处理器把所有异常压成 500（文档要求 400/404/409/422/429/499/500/504 分级错误码） |
| NFR-10 | 可移植性：全新 Windows ≤5 步全程离线部署 | `deploy/wheelhouse/`、`scripts/01..05`、`requirements.lock.txt`、`scripts/05_run_regression.ps1` | **未实现** | 无 `scripts/`、无 `deploy/`、无 `requirements.lock.txt`、无 `pyproject.toml` | requirements.txt 已按本机实际版本写死（与文档基线不同，见偏差表），但无 lock 全量哈希、无 wheelhouse、无 5 步脚本、无 `env-attest --verify`（当前 `cli env-attest` 只有 `--out`，无校验/退出码 33）、无 `04_start_server.ps1`/`05_run_regression.ps1` |

---

## 表 2 冲刺范围外缺口清单（文档要求但 `plan.md` 第 13~23 行范围未覆盖）

> 排序 = 建议的续作优先级。判据沿用文档：「省下的人时 ÷ 开发成本 ÷ 出错代价」。

| 需求编号 | 为何重要 | 建议的落地模块 |
|---|---|---|
| NFR-7 脱敏 | **合规红线**：文档规定姓名/学号/手机号/身份证/导师信息 5 类正则逐单元格扫描，命中即阻断入库；同时是"未发表内容不外传"的落地手段。缺失即比赛现场无法自证合规 | `mmagent/security/desensitize.py` + `config/desensitize.yaml` + `desensitize_hit` 表 + `api` 入库前钩子 |
| FR-9 字段级溯源 `profile_field_trace` + `review_flag` | 本场景的核心卖点就是"消灭沉默的错"：字段级 producer/time/version_id/upstream_refs 是 FR-9 与 FR-13 的共同基座，缺表则 P0-4 与 P0-5 的验收断言都无法闭合 | `store/schema.sql` 加表 + `store/profile.py::write_version` 同步写 trace + `mark_review` + `api/routes_profile.py` |
| 人机回环（LangGraph `interrupt()` + `SqliteSaver` 检查点） | 文档明确本场景主架构是课程 **7 号 Graph/Workflow**，三个卡点（口径僵持、sanity blocking、导出前合规）必须走 interrupt/Command(resume) 且状态可重建（NFR-5 丢失版本 ≤1）。现在 `coordinator.py` 只是纯 Python 状态机 + `HumanGateRequired` 异常占位，**暂停后无法恢复** | `mmagent/agents/coordinator.py` 升级为 StateGraph，检查点用 `langgraph.checkpoint.sqlite.SqliteSaver`（课件 demo11 有现成骨架） |
| NFR-4 / FR-2 真值冻结与回归集 | 决定"是否达标"的唯一依据。文档纪律：未对应测试用例的 P0 不得关闭；真值必须先冻结再跑，禁止手填。快照时 `tests/` 只有 `conftest.py`，等于 P0 全无可关闭路径 | `tests/golden/**` + `SHA256SUMS` + `cli freeze-golden` + `tests/regression/a_case_set.yaml` + `cli regression run/stats` + `mmagent/metrics.py` |
| FR-5 的"生成 + 1 轮修复"闭环（coder 真正接入 LLM） | 没有 `generate_script` 与 `repair_once`，"代码生成/求解/一键出图"只是"执行别人给的脚本"，P0-2 的主链路缺了头尾 | `mmagent/agents/coder.py` + `pipeline/repair.py` + `prompts/coder.system.j2`（补齐文档 5.3 的 JSON schema：module/filename/purpose/declared_outputs/code/figure_specs） |
| FR-2 表头/宽表解析修复 | 现缺陷让 6 张主附件各自只识别 1 个字段、口径冲突 0 条、抽检字段不存在——"体检"这一 P0 的实质结论目前不可用 | `mmagent/pipeline/intake.py`：多行表头探测（跳过标题行）+ 双层表头（附件6 合并单元格 14 处）+ 「门店×月份」熔成长表；同时补 `checks/units.py`、`checks/cardinality.py` |
| NFR-9 队列与分级错误码 | 文档要求三人会话排队、积压 ≤2 返回 429、solve 与 LLM 推理互斥；现在所有异常统一 500，前端/CLI 无法区分 `SANITY_BLOCKED(422)`、`IMMUTABLE_VERSION(409)`、`QUEUE_FULL(429)`、`LLM_BUDGET_EXCEEDED(499)` | `store` 加 `task_queue` 表 + `mmagent/app.py` 按 error_code 映射状态码 + `api/routes_admin.py` + `data/.inflight.lock` |
| FR-1 题面解析（archivist 真正接入） | P1 薄片，但也是 planner/coder 的输入源（子问题拆解 → 任务序列）；现 archivist 输出 schema 与文档五键 schema 不符，接上也没法用 | `mmagent/agents/archivist.py` + `prompts/archivist.system.j2` + `pipeline/parse.py` |
| FR-3 四类模型卡片 + data_check 硬校验 | planner 的"明显错配数 0"是防幻觉的关键闸门（Python 对照 field_mapping，不靠 LLM 自证） | `templates/cards/{ahp,entropy_topsis,mlr,linprog}.json` + `agents/planner.py` + `reports/fr3_datacheck.csv` |
| FR-8 进度守卫（文档要求随阶段一落地） | 零 LLM 成本却直接对应急救场景（截止前 6 h 最短可交版本、连续 6 h 无产出预警）；现在是纯空缺 | `mmagent/pipeline/watchdog.py` + `config/time_budget.yaml` + `cli`（NFR-3 同源） |
| NFR-10 离线部署 + lock | "换机器也能复现"是文档的可移植性硬指标，也是 NFR-4"冻结环境"的前提（env_attest 六项任一变化则回归结果失效） | `deploy/wheelhouse/` + `scripts/01..05*.ps1` + `requirements.lock.txt` + `cli env-attest --verify`（退出码 33） |
| NFR-6 可用性 + 7 个前端演示页 | 文档把 7 页定为固定交付物，是 3 人可用性测试（≤5 步/≤15 min）与多个 FR 的现场演示载体 | `frontend/*.html` + `tests/usability/usability_log.csv` + `cli usability check` |
| FR-7 论文装配 / FR-10 一致性校验 / FR-12 导出打包 | P1 薄片三件套，构成"写完能交"的闭环（装配 → 编号/数字一致性 → 打包附声明） | `pipeline/assemble.py` + `templates/paper/` + `checks/numbering.py` + `checks/consistency.py` + `cli export pack` |
| FR-4 检索（含原 FR-11） | 需要 `<RAG_ROOT>` 回填；文档列 P2，但 planner 的 card_id 引用依赖它 | `mmagent/rag/adapter.py` + `rag/vendor_path.py` + `source_ledger` 表 |
| 工程骨架收尾 | 快照时**仓库尚无 `.git`**（code freeze tag `mm-freeze-<赛日>` 与 `code_freeze_check.ps1` 无从落地）；无 `pyproject.toml`（pytest/ruff 配置）、无 `scripts/`、无 `docs/`（`RULE_JUDGEMENT.md`、`AI_DISCLOSURE_TEMPLATE.md`、`BASELINE_REPORT.md`） | 根目录 `pyproject.toml`、`git init`、`scripts/code_freeze_check.ps1`、`docs/` |

---

## 3. 冲刺范围内但快照时未达 DoD 的条目（与表 2 互补，属"计划内未完成"）

| plan.md 冲刺项 | 计划要求 | 快照实测 | 差距 |
|---|---|---|---|
| 1 仓库骨架 | `mmagent 包 + config + scripts + tests + artifacts` | mmagent ✓、config（仅 models.yaml）△、artifacts ✓、**tests（仅 conftest.py）** ✗、**scripts/ 不存在** ✗ | 缺 scripts/ 全部 5 步部署脚本；tests 未成形 |
| 2 FR-9 档案版本链 | write_version/adopt/AdoptedVersionImmutableError/草案裁剪 5 版/触发器 | 全部实现 ✓（`store/profile.py`、`schema.sql`） | 缺 `profile_field_trace`、`review_flag`、`discard`、`history`、`cli profile` |
| 3 FR-2 体检 | run_intake_audit → 4 件产出 | 4 件产出 ✓ 且非空 | **表头解析缺陷导致 6 张主附件字段数=1、冲突 0 条**（见 FR-2 行） |
| 4 FR-5 确定性件 | guard + runner + sanity + repair 骨架 | guard ✓、runner ✓、sanity ✓、repair（骨架抛 NotImplementedError）△ | sanity 出参结构不符；guard 缺绝对路径写盘检查；runner 缺日志落盘与出参结构 |
| 5 FR-6 | plotstyle + render（dpi300、缺字形检测） | ✓（`_png_dpi` 校验 300 + `available_cjk_font()` 告警） | 缺 manifest 九列、命名规范、numbering 校验、re-plot CLI |
| 6 FR-13 | disclosure md+docx | ✓ 双份生成 | artifacts 表缺 `upstream_refs`；人改比例口径不符；无 audit |
| 7 agents/base + coordinator | Ollama 客户端、JSON 强制、schema 校验、重试/降级、prompt 版本记录 | ✓ 全部实现（`base.py:50/81/124`：httpx `/api/chat` + `format=json`、三层降级链、`_validate_required`、`metric_obs`+`artifacts` 落库） | **schema 校验只有 required 顶层键检查**（无 jsonschema/type 校验）；4 个角色输出 schema 均为骨架（archivist 要求 `subproblems` 等五键，实际只校验 `summary`） |
| 8 FastAPI + cli | 最小骨架 + cli 入口 | ✓ `app.py`（5 路由 + 统一错误体）、`cli.py`（db init / intake / env-attest / netguard-check） | 文档 4.1 路由表 26 条 → 实际 **5 条**；cli 文档子命令 12 个 → 实际 **4 个** |
| 9 测试 | FR-9/FR-2/guard/sanity/disclosure 单测，pytest 跑绿 | ✗ 仅有 `tests/conftest.py` | P0 全部无自动化用例 |
| 10 收尾 | PROGRESS.md + final-summary.md | 由队长收尾，不在本次核对范围 | — |

---

## 4. 技术选型偏差（文档 vs 实现，均为已记录的降级，非缺陷）

| 项 | 文档规定 | 实际 | 影响 |
|---|---|---|---|
| Python | CPython **3.11.9**（明确"不使用 3.12+ 以免 wheel 缺失"） | **3.13.12**（`README.md` KNOWN_ISSUES + `config/models.yaml` 侧记） | NFR-10「冻结环境六项」之一已偏离；回归结果按文档口径属"非冻结环境" |
| 生成模型 | `qwen3.5` / 降级 `qwen3:4b` | `qwen3.5:latest` / 降级 `qwen3.5:0.8b`（`config/models.yaml`） | 本机无 qwen3:4b；0.8B 降级档能否稳定产出可执行脚本未实测 |
| 依赖版本 | fastapi 0.115.6 / pandas 2.2.3 / numpy 1.26.4 / matplotlib 3.9.2 / python-docx 1.1.2 / pytest 8.3.4 等 | fastapi 0.115.6 ✓ / **pandas 3.0.6** / **matplotlib 3.11.2** / **python-docx 1.2.0** / **pytest 9.1.1** / 新增 **httpx 0.28.1** | 无 `requirements.lock.txt`，冻结环境无法自证；依赖新增未回写文档 |
| 编排框架 | 7 号 Graph/Workflow：StateGraph + interrupt + SqliteSaver | 纯 Python 状态机骨架（`coordinator.py`），**未引入 langgraph** | 文档"不引框架、按同等职责自研薄封装"与 1.2 节"7 号 Graph 架构"之间存在张力，`coordinator.py` 注释已标 `TODO(LangGraph)`——**需要队长拍板自研还是引入 langgraph** |
| 出图风格函数 | `plotstyle.apply_cn_style()` | `apply_paper_style()` | 命名不一致，文档验收命令/测试用例对不上 |

---

## 5. 里程碑与交付物核对

| 里程碑 / 交付物 | 状态 |
|---|---|
| MS0：基线数字回填需求（时延/内存/tokens/s 实测报告 + BASELINE_REPORT.md） | **未达成**（`artifacts/reports/env_attest.json` 已生成，但无基线实测报告、无 `cli env-attest --verify`、无 `docs/BASELINE_REPORT.md`） |
| 阶段 0 交付物：规则判断书、AI 声明模板、基线报告 | **0/3**（无 `docs/`） |
| MS2：P0 五条全量交付 + first_run_rate ≥70% + 端到端 ≤15 min | **未达成**（P0 五条均部分实现，无回归数据） |
| 阶段 1 交付物：体检 4 件套 / 生成出图链路 / profile 版本链 / ai_disclosure.{md,docx} / A 题回归报告(≥50次) | **3/5 存在**（体检 4 件套 ✓ 有产物但结论失真、版本链 ✓、披露文件 ✓ 可生成；出图链路 ✗ 无生成/修复；回归报告 ✗） |
| 阶段 2 交付物：题面 JSON、4 类卡片、五章 docx、一致性报告、最短可交清单、可用性记录、A2024_pack.zip | **0/7** |
| 阶段 3 交付物：告警值守记录、恢复演练报告、RETRO.md、增量清单 | **0/4** |
| 代码冻结点（`git tag mm-freeze-<赛日>` + `code_freeze_check.ps1`） | **不可执行**（仓库尚无 `.git`，无脚本） |

---

## 6. 遗留问题（需队长/用户确认）

1. **架构拍板**：协调层是继续自研纯 Python 状态机，还是按文档 1.2/3.2 节改造成 LangGraph StateGraph + `interrupt()` + `SqliteSaver`？这决定 FR-9/NFR-5 的"暂停-恢复-重建"验收怎么写，也决定是否新增 langgraph 依赖（当前 requirements.txt 无）。
2. **接口契约粒度**：文档 4.1 给出 26 条路由与 9 个错误码，当前只有 5 条路由且异常统一 500。续作是按文档逐条补齐，还是先按"最小可演示闭环"裁到 8~10 条？需队长定线。
3. **FR-2 是否重做**：修表头/宽表解析属于"已有成果返工"，会吃掉 6 张主附件的全部体检结论；如果时间紧，是否接受"只体检 `总表.xlsx` + 预处理 xlsx，附件1~6 另存为 xlsx 后重传"（文档 11.1 风险表已给该降级动作）？
4. **真值冻结由人完成**：`tests/golden/intake/spotcheck_expect.json`（含 214 行/缺失率等数字）文档要求"freeze-golden --write 生成后人工核对并签署"，**不能由 AI 填**——需何昆阳本人完成，续作排期要留出人工额度。
5. **本报告为 18:58 快照**：任务 #2 仍在进行，19:00 后新增的 `api/routes_*.py`、`agents/*.py` 可能已被继续修改；建议在冲刺收尾后由我或 Tess 重跑一次核对（只读复跑命令：`python probe_docx.py` + 表 1 的"证据文件"列逐项 grep）。
