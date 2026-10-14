# 测试报告 — P0 确定性模块（FR-9 / FR-2 / FR-5 / 数据核查 / FR-6 / FR-13）

- 测试工程师：Tess（**只写 `tests/`，未改动 `mmagent/` 任何源码**）
- 日期：2026-10-13（第二段最终快照）
- 测试命令：`cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q`
- 解释器：`H:\mm-agent\.venv\Scripts\python.exe`（Python 3.13，pytest 9.1.1 / pandas 3.0.6 /
  xlrd 2.0.2 / openpyxl 3.1.5 / matplotlib 3.11.2 / python-docx 1.2.0 / Pillow 12.3.0 / PyYAML 6.0.3）

## 结论（第二段最终）

```
RUN#1 2026-10-13 20:34:20 → 20:35:12   189 passed, 1 xfailed in 48.62s
RUN#2 2026-10-13 20:39:34 → 20:40:21   189 passed, 1 xfailed in 43.73s   ← 终版改名后的复核跑
（共 190 个用例，0 failed、0 skipped）
```

- RUN#2 是在 `att5_problem→att5_complaint`、`att6_profit_quota→att6_profit` 改名**之后**、对**终版冻结修订**
  的复核跑，结果与 RUN#1 完全一致 —— 说明改名未打破任何用例（用例不硬编码 table_id，见 §0.2）。
- **第一段记录的 7 条源码/性能缺陷已全部由实现方修复**（FR9-01 `INSERT OR REPLACE` 绕过 adopted 不可变、
  FR5-01/02/03 guard 三类绕过、SANITY-01 NaN 误报越界、FR2-02 门店列周期误标、PERF-01 每连接重复设 WAL），
  对应 xfail 已按「转绿即摘标记」逐条转正。
- **唯一保留的 xfail = 1 条**：`INSERT OR REPLACE` 违规**无审计留痕**（DB 触发器 `RAISE(ABORT)` 直接回滚语句，
  应用层无机会写 `events` 表）。属「防止绕过」目标的审计链补完项，见 ISSUE-FR9-03。
- FR-2 重构交付后，原先 17 条「重构后行为预备用例」（`tests/test_fr2_intake_rework.py`）**已全部并入
  `tests/test_fr2_intake.py` 并转正**，过期文件已删除，避免两套口径并存。
- 报告口径：`xfail` = 期望行为未实现（断言的是「应该怎样」，当前不满足），**不是跳过、也不是弱化断言**；
  本报告 0 个 `skip`，全部用例在本地真实执行。

---

## 0. 被测源码修订（冻结基线）

> **本基线仅代表 2026-10-13 20:34–20:35 我最后一次全量跑绿时的时点。** 源码在此之后仍可能变动
> （实现方仍在收尾），若 `mmagent/` 或 `config/*.yaml` 任一文件 md5 与下表不一致，**本报告结论即失效**，
> 需对变动模块重跑；比对命令见 §6。

- 冻结时点：**2026-10-13 20:40**（RUN#2 结束时刻；RUN#1 20:35 与 RUN#2 20:40 之间上述 md5 **未变**，故基线对两次运行同时成立）
- 覆盖范围：`mmagent/**/*.py`（36 个）+ `mmagent/store/schema.sql`（1 个）+ `config/*.yaml`（4 个）= **41 个文件**
  （另附 `mmagent/__init__.py` 等包初始化文件，合计 **45 条**）

| 文件 | md5 | mtime | 字节 |
|---|---|---|---|
| `mmagent/__init__.py` | `4a6a8e098716598e8fbdcaf6587eb494` | 2026-10-13 18:46:02 | 242 |
| `mmagent/agents/__init__.py` | `8958ee9861d030bc07a1bfa55500811e` | 2026-10-13 18:52:20 | 105 |
| `mmagent/agents/archivist.py` | `249a90d8e078e33adb1bfe1c62805b19` | 2026-10-13 18:52:50 | 484 |
| `mmagent/agents/assembler.py` | `e49cd15dfc6aae212092d9ac1ae9a326` | 2026-10-13 18:52:50 | 419 |
| `mmagent/agents/base.py` | `c7d7cb8ebfff3868a5ee804c51b0d9c4` | 2026-10-13 19:49:28 | 8120 |
| `mmagent/agents/coder.py` | `b84d1869464de0f98743e1de5e525a94` | 2026-10-13 19:13:49 | 2637 |
| `mmagent/agents/coordinator.py` | `9a51b63293f8c1bd5fb5804775e6a04a` | 2026-10-13 18:52:20 | 5348 |
| `mmagent/agents/planner.py` | `06d80d7c9eca6c18f35d32e5d4af35da` | 2026-10-13 18:52:50 | 380 |
| `mmagent/api/__init__.py` | `0408b1a81ec23464acc4ba795b09c44b` | 2026-10-13 18:55:05 | 72 |
| `mmagent/api/routes_compliance.py` | `4118315be65824862fcc4478abd23686` | 2026-10-13 18:55:05 | 828 |
| `mmagent/api/routes_intake.py` | `95a0f47cc62f38f0249314aa04b6bc55` | 2026-10-13 18:55:05 | 781 |
| `mmagent/api/routes_profile.py` | `b5adfc98f4b8e8850113f4284660c859` | 2026-10-13 18:55:05 | 532 |
| `mmagent/api/routes_solve.py` | `0c108d54faffb0d7768412ad712b5576` | 2026-10-13 18:55:05 | 984 |
| `mmagent/app.py` | `bf75c1ee0d2859b74a3c4bbf8ef41f76` | 2026-10-13 19:03:38 | 3831 |
| `mmagent/checks/__init__.py` | `85a4b9f3b8b790d1d063cbaacfa6d88f` | 2026-10-13 18:50:23 | 77 |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` | 2026-10-13 20:02:17 | 4753 |
| `mmagent/cli.py` | `1817d91cc825bcef6fb5bc2ca816c177` | 2026-10-13 19:11:23 | 8694 |
| `mmagent/compliance/__init__.py` | `da946c2bc96df92a00621a3649d2f5f0` | 2026-10-13 18:51:11 | 57 |
| `mmagent/compliance/disclosure.py` | `3106108e7f01b96cc40eb249c618e57a` | 2026-10-13 18:51:11 | 5700 |
| `mmagent/pipeline/__init__.py` | `de489c32e771eb400d64872855bad556` | 2026-10-13 18:48:30 | 79 |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` | 2026-10-13 20:01:55 | 4557 |
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` | 2026-10-13 20:15:24 | 42831 |
| `mmagent/pipeline/intake_llm.py` | `075c992dffe0360ed17ed79c8f1815e4` | 2026-10-13 18:49:24 | 1003 |
| `mmagent/pipeline/render.py` | `05158617438b2cdf7d468ad974a3d782` | 2026-10-13 18:50:23 | 3569 |
| `mmagent/pipeline/repair.py` | `ecfedcf241c542f3b826dbd4e00ae76f` | 2026-10-13 19:13:49 | 2467 |
| `mmagent/pipeline/runner.py` | `d2c539fa51c6025bdf59ffaf8b91e67f` | 2026-10-13 19:30:36 | 2981 |
| `mmagent/plotstyle.py` | `0204b4b1aa4187617a2c9a3f23c7b7f4` | 2026-10-13 18:50:23 | 1164 |
| `mmagent/rag/__init__.py` | `06650cf912ef9ae4811dfb83f0541b61` | 2026-10-13 18:55:05 | 86 |
| `mmagent/rag/adapter.py` | `277adf8e820bf7d1588e283b36adc956` | 2026-10-13 18:55:05 | 1071 |
| `mmagent/security/__init__.py` | `43d24370e6f8fb055cbc677c9afe8cb5` | 2026-10-13 19:03:16 | 83 |
| `mmagent/security/desensitize.py` | `bab056e8af46652822505cfa72e83d67` | 2026-10-13 19:03:26 | 6576 |
| `mmagent/store/__init__.py` | `267538fd18ac9d99490a892e98a4d7bc` | 2026-10-13 18:46:33 | 102 |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` | 2026-10-13 20:01:18 | 1706 |
| `mmagent/store/profile.py` | `184eece639f21218c6085c92c6b65f46` | 2026-10-13 18:47:20 | 8603 |
| `mmagent/tools/__init__.py` | `5b27574faea75e05d52270c7f6b71e5b` | 2026-10-13 18:54:57 | 179 |
| `mmagent/tools/compliance_tool.py` | `624c0bf62edc5c5af1819ae780ce42bf` | 2026-10-13 18:54:57 | 762 |
| `mmagent/tools/intake_tool.py` | `3c47121aaed3cc2b70822eb32934cb73` | 2026-10-13 18:54:57 | 474 |
| `mmagent/tools/plot_tool.py` | `e4ed45a34f27ae13a5156474fbc03ec3` | 2026-10-13 18:54:57 | 657 |
| `mmagent/tools/profile_tool.py` | `e6af9ce1a5f93271356084f56a04ff2f` | 2026-10-13 18:54:57 | 1211 |
| `mmagent/tools/solve_tool.py` | `58c92635dd7864225777c86ae5ee84a9` | 2026-10-13 19:14:24 | 3388 |
| `config/desensitize.yaml` | `0b00907be7705ef2227429eea4b117c1` | 2026-10-13 19:03:16 | 1744 |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` | 2026-10-13 20:18:32 | 3124 |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` | 2026-10-13 19:55:05 | 482 |
| `config/models.yaml` | `a4f4a5a6cb6e690ef4d3203aac0a6f19` | 2026-10-13 19:49:14 | 1178 |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` | 2026-10-13 20:01:09 | 4400 |

> 机器可读副本：`.agent-runs/2026-10-13/task-001/md5-baseline-final.md`（同表，含表头）。
>
> 与队长转述的实现方自报 md5 的差异：`intake.py` 已从 `4a20fb0a…` 前进到 `b16d96b9…`
> （实现方补 `skipped_files` 留痕 + `numeric_sum` + `value_counts` + table_id 语义化改名所致），
> `field_aliases.yaml` 亦在 20:18 更新。**以本表为准**——它是走过 189 绿的实际修订。

### 0.1 队长指定的 8 项终版基线（磁盘实测复核 ✅ 逐字符一致）

> 队长先后给出过三版 README 哈希（中间态），此处以**我实测磁盘**的值为准，并已与队长最终版对齐。
> `README.md` 仅文档、不参与 pytest；列出以便收尾文档对齐。

| 文件 | md5（磁盘实测 20:39） |
|---|---|
| `mmagent/pipeline/intake.py` | `b16d96b97e353abd151f594ad6571f9f` |
| `config/field_aliases.yaml` | `ecbd5d8ab4fa0e402255028bcefdc3ca` |
| `README.md` | `2175bb71faab5b4618373e8ca618fc92` |
| `mmagent/store/schema.sql` | `dd65844bc5d0815d6c377c2b7c4dbfd4` |
| `mmagent/pipeline/guard.py` | `7bf8d6bf77b805594a2c1cb1b1de52a6` |
| `mmagent/store/db.py` | `084cfc517f1e2cce456b307a3f5fcee0` |
| `mmagent/checks/sanity.py` | `e25b0d7716a209cfbcf159b155c524d9` |
| `config/intake_scope.yaml` | `72fff50745f8805a6c1a5a80ce2750e9` |

### 0.2 终版 `table_id` 与关键产物现状（我独立实测复核）

- **终版 table_ids（全目录 8 表）**：`summary_master`（×2 sheet）、`att1_satisfaction`、`att2_service`、
  `att3_clean`、`att4_complaint`、`att5_complaint`、`att6_profit`；字段数 153、冲突数 5。
  （`att5_problem` / `att6_profit_quota` 已按文档口径改名为 `att5_complaint` / `att6_profit`，
  旧名仅在 `config/field_aliases.yaml` 的 `legacy_table_ids` 里作兼容键。）
- **测试未硬编码任何 table_id**（已 `grep` 确认 `tests/` 下 0 处命中）——所有 FR-2 集成断言都通过
  `source_file` 前缀 / `(source_file, sheet_name)` 定位，或断言「table_id 互不重复」这类结构性事实。
  因此本轮改名**未打破任何用例**，后续再改名也不会误伤。
- **附件5 全空两列的 `blank_as_zero` 覆盖已修好**（实测）：`1月`/`4月` 为 `missing_rate=0.0`、
  `raw_missing_rate=1.0`、`blank_as_zero=True`、`all_blank_as_zero=True`、`dtype='empty'`；
  对照 `2月` 为 `all_blank_as_zero=False`。
- **全产物「`raw_missing_rate>=1.0` 且未标 `all_blank_as_zero`」的列 = 0 条**（实测）。
- **att6 `numeric_sum` 合计 = 58633.0**（= 27289.0 定额 + 31344.0 实际利润）；`att6` 的 `12月.评级`
  `value_counts = {合格:156, 不合格:1}`、`field_raw=''`、语义名在 `field_name`。
- 说明：评级列断言按 `period=='M12' and role=='评级'` 定位，**不依赖 `field_raw` 或 `field_name` 的字面值**，
  故队长提示的「value_counts 挂在 `field_name`（field_raw 为空）」对本套用例无影响，已天然兼容。

**测试期修补记录（第一段 → 第二段之间落地的修复）**

| 模块 | 变更 | 影响 |
|---|---|---|
| `mmagent/pipeline/guard.py` 20:01:55 | 黑名单 → **import 白名单 + 危险调用黑名单** | ISSUE-FR5-01/02/03 全部解决 |
| `mmagent/checks/sanity.py` 20:02:17 | `_check_range` 用 `notna() & (`…`)` 剔除 NaN | ISSUE-SANITY-01 解决 |
| `mmagent/store/schema.sql` 20:01:09 | 新增 `trg_profile_adopted_guard_insert`（BEFORE INSERT 守卫） | ISSUE-FR9-01 主漏洞解决 |
| `mmagent/store/db.py` 20:01:18 | `journal_mode=WAL` 移入 `init_db`；连接级加 `recursive_triggers=ON` | ISSUE-PERF-01 解决 |
| `mmagent/pipeline/intake.py` 20:15:24 + `config/*` | FR-2 重构（召回末列/三层表头/三口径/留痕清单/白名单/numeric_sum/value_counts） | 全部 FR-2 用例转正 |

---

## 1. 覆盖清单（用例 → success_criteria）

| 测试文件 | 覆盖 FR | 用例数 | 结果 |
|---|---|---|---|
| `tests/test_fr9_profile.py` | FR-9 档案版本链 | 32 | 31 passed, 1 xfailed |
| `tests/test_fr2_intake.py` | FR-2 附件体检 | 51 | 51 passed |
| `tests/test_fr5_guard_runner.py` | FR-5 守卫 + 沙箱执行 | 43 | 43 passed |
| `tests/test_sanity.py` | 数据核查 | 24 | 24 passed |
| `tests/test_fr6_render.py` | FR-6 出图 | 19 | 19 passed |
| `tests/test_fr13_disclosure.py` | FR-13 合规声明 | 21 | 21 passed |
| **合计** | — | **190** | **189 passed / 1 xfailed / 0 failed / 0 skipped** |

### FR-9 档案版本链（`tests/test_fr9_profile.py`，32 例）

| success_criteria | 覆盖用例 |
|---|---|
| 临时库 + schema 建表 + 触发器存在 | `test_init_db_幂等_且建出版本链表与不可变触发器` |
| 连续写 8 次 draft 恰好留 5 版 | `test_连续写8次草案_只保留最近5版且版本号严格递增`、`test_草案裁剪不影响不同档案` |
| 写入序列严格递增、不跳号 | 同上 + `test_adopt_后版本号仍严格递增`、`test_并发写入不跳号且版本号互不相同` |
| adopt 只追加、旧行不变 | `test_adopt_只追加新行_旧草案行原封不动`、`test_再次adopt_旧adopted行保持adopted且不可变_链可追溯` |
| supersedes 链可查 | `test_再次adopt_...链可追溯`、`test_adopt_继承源版本patch与upstream_refs` |
| 裸 SQL UPDATE/DELETE adopted → 触发器拦截 | `test_直接SQL_UPDATE_adopted行_触发器拦截`、`test_直接SQL_DELETE_adopted行_触发器拦截` |
| 应用层 `AdoptedVersionImmutableError` | `test_try_update_row_对adopted行抛AdoptedVersionImmutableError` |
| 事件表 `error_code='ADOPTED_IMMUTABLE'` | `test_不可变违规写入事件表ADOPTED_IMMUTABLE`、`test_合规更新不产生ADOPTED_IMMUTABLE事件` |
| **REPLACE 绕过防线（回归 ISSUE-FR9-01）** | `test_INSERT_OR_REPLACE_不能覆盖adopted行`、`test_INSERT_OR_REPLACE_失败后adopted行数据未被改动`、`test_开启recursive_triggers后_REPLACE被现有触发器拦下`、`test_INSERT_OR_REPLACE_违规留下事件留痕`(xfail) |
| `read_profile('last')` 语义 | `test_read_profile_last尚无adopted时退回最新草案`、`test_read_profile_last优先返回最新adopted而非更新的草案`、`test_read_profile_可按下标读历史草案` |
| JSON 往返 + UTF-8 | `test_patch复杂结构JSON往返一致`、`test_upstream_refs复杂结构JSON往返一致`、`test_patch以UTF8存储不进ensure_ascii转义` |
| 事件流水完整性 | `test_每次写入草案都留下一条事件流水` |
| 连接级 PRAGMA 行为（替代 flaky 计时） | `test_连接仍保持WAL与外键约束` |
| 边界/异常 | `test_read_profile_paths只返回指定顶层键`、`test_read_profile_不存在的版本抛KeyError`、`test_read_profile_不存在的档案抛KeyError`、`test_adopt_不存在的源版本抛KeyError`、`test_try_update_row_对draft行可正常修改`、`test_try_update_row_无字段时不改动任何行`、`test_写入时upstream_refs缺省为空列表` |

### FR-2 附件体检（`tests/test_fr2_intake.py`，51 例）

| success_criteria | 覆盖用例 |
|---|---|
| 7 件产物齐全且非空 | `test_产出全部体检产物且均非空`、`test_空数据集目录_仍产出产物且字段为空` |
| schema_report 结构（tables/fields/skipped_files） | `test_schema_report含tables与fields两段` |
| 表级三口径行数（physical/effective/blank/row_count） | `test_表级记录含三口径行数`、`test_真实附件_三口径行数为冻结真值` |
| 表标题 + 真表头识别，大标题不得成列名 | `test_标题行被捕获且不计入数据行`、`test_真实附件_表头是真实列名而非大标题` |
| 字段数冻结真值 13/25/25/13/13/37 | `test_真实6个附件_各产出表级记录且字段数达标`、`test_真实附件_字段数为冻结真值` |
| 6 张附件有效门店行均 157 | `test_真实附件_有效门店行均为157`、`test_真实附件1_三口径行数` |
| **表级 `numeric_sum` golden** | `test_真实附件_数值合计为冻结golden`（附件4=432.0 / 附件5=131.0 / 附件6 定额=27289.0 / 实际利润=31344.0 / 合计=58633.0） |
| **评级列 `value_counts` golden** | `test_真实附件_12月评级分布为冻结golden`（附件2 {合格144,不合格13} / 附件3 {合格154,不合格3}） |
| blank_as_zero 双缺失率口径分离 | `test_缺失率双口径_空格按0列mr为0而raw大于0`、`test_未声明空格按0时口径与原始一致`、`test_全空列原始缺失率为1`、`test_真实附件_空格按0列missing_rate为0` |
| 表级 fillna_rule 来自表标题 | `test_表格级fillna_rule来自表标题` |
| dtype 判定（int/float/str/empty + 三类齐备） | `test_数值月份列dtype为int`、`test_脏值使该列dtype降级为str`、`test_真实附件6_三类dtype齐备`、`test_真实附件4_月份列为整数` |
| period（Mnn）/ canonical / role / entity_scope | `test_月份编号列period为M期号`、`test_门店标识列不带周期语义`、`test_canonical按别名表解析` |
| 字段归一化（去空白 + 小写） | `test_字段归一化去空白并小写` |
| 取列纪律：非月度列入 unmapped 留痕 | `test_非月度列不入字段表而记入未映射留痕`、`test_空表头列有数据时记入未映射清单` |
| 脏单元格清单（17 处，原值不改写） | `test_脏值清单记录文本混入数值列`、`test_真实附件_留痕清单结构完整` |
| column_profiles / field_mapping.csv 契约 | `test_column_profiles覆盖全部字段`、`test_字段映射csv_含必需列且每字段一行`、`test_字段映射csv_带出canonical与周期`、`test_真实附件_字段映射csv行数等于字段数` |
| 摘要 md 小节 | `test_体检摘要md含关键小节`、`test_体检摘要md列出留痕清单说明` |
| 口径冲突 4 必需键 + 证据可回溯 | `test_冲突记录含必需键与可核查证据`、`test_按冲突类型可定位type字段`、`test_冲突证据可回溯到源文件`、`test_真实附件_冲突记录结构完整` |
| **输入白名单 + skipped_files 留痕** | `test_白名单外文件被跳过且留痕`、`test_真实附件_白名单跳过预处理文件`、`test_真实目录_白名单跳过两个预处理文件` |
| 其它边界 | `test_空sheet被跳过`、`test_非表格文件被忽略`、`test_输出目录不存在时自动递归创建`、`test_返回汇总dict的字段与产物一致`、`test_多文件各自独立产出表级记录` |

### FR-5 确定性执行（`tests/test_fr5_guard_runner.py`，43 例 = 14 参数化 + 29）

| success_criteria | 覆盖用例 |
|---|---|
| 拦截违规 import/调用（14 种源码） | `test_guard_拦截违规脚本_抛ScriptGuardError`（参数化 ×14） |
| 违规信息含行号与全部违规点 | `test_guard_违规信息包含行号与禁令说明`、`test_guard_多处违规全部上报` |
| 白名单：危险顶层模块一律拒绝 | `test_guard_危险顶层模块被白名单拒绝`（os/sys/importlib/shutil，即便只用无害成员） |
| 干净脚本 + 常用本地库放行 | `test_guard_干净脚本通过且返回None`、`test_guard_空脚本通过`、`test_guard_白名单化后常用本地库仍须放行` |
| 语法错误脚本被拦截 | `test_guard_语法错误脚本被拦截` |
| **历史绕过写法已封堵（回归）** | `test_guard_拦截from_os_import_system绕过写法`（FR5-01）、`test_guard_拦截importlib动态导入绕过写法`（FR5-02）、`test_guard_拦截第三方HTTP库httpx` / `test_guard_拦截标准库http_client`（FR5-03） |
| runner 正常态 | `test_runner_正常脚本_返回码0且ok为真`、`..._产出文件被列出`、`..._捕获stdout且时长为正`、`test_runner_脚本在workdir下执行`、`test_runner_无产出脚本_produced_files为空`、`test_runner_workdir不存在时自动创建`、`test_runner_返回报告包含全部约定键`、`test_runner_子进程使用同一解释器`、`test_runner_支持相对script路径与workdir` |
| runner 崩溃态 | `test_runner_崩溃脚本_返回码非0且stderr非空`、`test_runner_语法错误脚本_返回码非0`、`test_runner_崩溃脚本即使声明manifest也不ok` |
| runner 超时态 | `test_runner_超时脚本_标记timed_out且不阻塞`、`test_runner_超时脚本_实际耗时接近超时阈值` |
| manifest 契约 | `test_runner_manifest齐全时produced_files取manifest`、`test_runner_manifest缺文件时ok为假`、`test_runner_manifest_json损坏时不崩溃` |
| 守卫→执行端到端 | `test_守卫通过后执行成功_端到端` |

### 数据核查（`tests/test_sanity.py`，24 例）

| success_criteria | 覆盖用例 |
|---|---|
| 得分 0~100 / 占比 0~1 / 利润 ≥0 越界 | `test_得分越界被拦截且blocking`、`test_占比大于1被拦截`、`test_利润为负被拦截`、`test_内置关键词匹配大小写不敏感`、`test_内置关键词命中英文列名ratio` |
| **NaN 不参与越界（回归 ISSUE-SANITY-01）** | `test_缺失值不应被判为越界值`、`test_全空数值列不触发越界_缺失由not_null规则单独管` |
| 主键重复 | `test_主键重复命中unique规则`、`test_主键唯一时不报violation` |
| 干净数据 passed=True | `test_干净数据通过`、`test_空结果表集合通过`、`test_布尔列不触发得分规则`、`test_非数值列不误报` |
| blocking 语义 | `test_非阻断规则_违规但blocking为假`、`test_阻断与非阻断并存时blocking为真` |
| 自定义规则 range/not_null/row_count_match | `test_自定义range规则生效`、`test_自定义range仅设上限`、`test_not_null规则命中空值`、`test_row_count_match规则命中行数不一致`、`test_row_count_match行数一致时不报`、`test_规则指向不存在的表或列时静默跳过` |
| 结构/边界 | `test_violation结构含约定键`、`test_violation最多保留20个行号`、`test_多个表多个列同时越界` |

### FR-6 出图（`tests/test_fr6_render.py`，19 例）

| success_criteria | 覆盖用例 |
|---|---|
| 出 PNG 且非空 / 零规格仍出 manifest | `test_折线图产出PNG文件`、`test_零规格时仍产出带表头的manifest` |
| PNG 元数据 dpi == 300 | `test_PNG元数据dpi为300`（PIL 独立读 pHYs 复核） |
| manifest csv 行数/表头/路径一致 | `test_manifest_csv表头固定且每图一行`、`test_manifest中路径与实际落盘文件一致`、`test_manifest含图型与来源表字段` |
| 图号生成 | `test_未指定figure_id时按顺序编号` |
| 4 种图型 | `test_四种图型都支持`、`test_hist只需x列`、`test_图题写入且不报错` |
| 异常分支 | `test_不支持的图型抛ValueError`、`test_结果表中无该表时抛KeyError`、`test_列名不存在时抛KeyError`、`test_首图异常时不产出后续图`、`test_输出目录不存在时自动创建` |
| 论文出图风格 | `test_论文风格设置dpi300与负号ASCII`、`test_论文风格幂等`、`test_中文字体查询返回字符串或None`、`test_中文字体候选列表含宋体黑体` |

### FR-13 合规声明（`tests/test_fr13_disclosure.py`，21 例）

| success_criteria | 覆盖用例 |
|---|---|
| md + docx 双文件非空 | `test_生成md与docx双文件且非空`、`test_重复生成时覆盖且内容自洽` |
| md 含四关键字段小节 | `test_md含四个关键字段小节`、`test_md记录档案与版本号`、`test_版本号为空时写last` |
| 人改比例三种来源 | `test_显式人改比例写入并注明口径`、`test_人改比例由事件流水统计`、`test_无事件数据时使用保守默认值`、`test_零比例也如实写入`、`test_人改比例格式化为百分数`（×3） |
| 模型与提示词溯源 | `test_模型标签与提示词版本来自溯源表`、`test_无可溯源数据时写占位` |
| 数据来源 | `test_自定义数据来源被列出`、`test_未指定数据来源时用默认值`、`test_声明含合规承诺` |
| docx 内容一致 | `test_docx可读回且含四个小节`、`test_docx与md人改比例一致`、`test_docx含自定义数据来源` |
| 目录自动创建 | `test_输出目录不存在时自动递归创建` |

---

## 2. 用例明细（本轮变更 / 新增用例）

> 第一段 168 例的完整明细见本报告历史版本；本节只列**第二段相对第一段发生变更或新增**的用例，
> 其余用例断言未改动（全部保持绿）。

### 2.1 FR-9（变更 3 条）

| 用例 | 输入 | 预期 | 状态 |
|---|---|---|---|
| `test_INSERT_OR_REPLACE_不能覆盖adopted行` | 裸连接 `INSERT OR REPLACE` 撞已 adopted 的 (profile_id, version_id) | `sqlite3.IntegrityError` 且消息含 `adopted immutable` | 摘 xfail，**已绿** |
| `test_INSERT_OR_REPLACE_失败后adopted行数据未被改动` | 同上；前后 `SELECT *` 回读比对 | REPLACE 失败后整行**字节级不变**（不只断言抛异常） | 摘 xfail，**已绿** |
| `test_INSERT_OR_REPLACE_违规留下事件留痕` | 同上触发一次 | `events` 里出现 `error_code='ADOPTED_IMMUTABLE'` | 仍 xfail（见 ISSUE-FR9-03） |

### 2.2 FR-2（修正 6 条 + 新增 6 条）

| 用例 | 输入 | 预期 | 说明 |
|---|---|---|---|
| `test_schema_report含tables与fields两段` | 合成数据 | `{tables,fields} ⊆ keys` 且含 `skipped_files` | **改为子集断言**（实现新增 `skipped_files` 段；删段/改名仍会红） |
| `test_白名单外文件被跳过且留痕` | 目录含 `预处理-有资格月度.xlsx` | `skipped_files` 为 `[{file, reason}]`，含该文件且 reason 非空 | **修正**：原断言当成字符串列表 |
| `test_字段归一化去空白并小写` | 表头 `["月 份 门 店", " Store ID ", " 1 "]` | field_norm 依次 `月份门店` / `storeid` / `1月` | **重设计**：原用例假定「任意列都进字段表」，实际口径只收门店列 + 月份组列 |
| `test_非月度列不入字段表而记入未映射留痕` | `["门店","统计日期"]` + 1 行 | 字段仅 `门店`；`统计日期` 进 `unmapped_columns`（含 non_empty/sample_values） | **取代**原 `test_日期型字段被识别为datetime`（见 §5 观察项 O-04） |
| `test_体检摘要md含关键小节` | 合成数据 | 含「各表行数（三口径）/ 缺失率 Top10 / 口径来源说明（fillna_rule）/ 未映射列留痕 / 脏单元格 / 口径冲突清单」 | **修正**：原断言的「## 缺口」小节不存在；「缺失率 Top10」括号后缀随数据切换，改断言稳定前缀 |
| `test_真实目录_白名单跳过两个预处理文件` | 真实全目录 | `skipped_files==[预处理-有资格月度.xlsx, 预处理2-排名及其获奖名单.xlsx]`；`unmapped_count==2` | **修正**：skipped_files 取值方式 |
| `test_真实附件_字段数为冻结真值`（新） | 复制 6 个 .xls | `{附件1:13, 附件2:25, 附件3:25, 附件4:13, 附件5:13, 附件6:37}` | 由原「≥13 / ≥35」弱断言升级为**精确真值** |
| `test_真实附件_数值合计为冻结golden`（新） | 6 .xls | 附件4=432.0、附件5=131.0、附件6 定额=27289.0、实际利润=31344.0、合计=58633.0（`pytest.approx(abs=1e-6)`） | **直接断言 golden**（载体 = 表级 `numeric_sum`） |
| `test_真实附件_三口径行数为冻结真值`（新） | 附件1 / 附件6 | 附件1 (160,157,3)；附件6 (157,157,0) | 行数口径精确化 |
| `test_真实附件_12月评级分布为冻结golden`（新） | 附件2/3 的 M12 评级列 | `value_counts` 精确等于 golden 分布；samples ⊆ {合格,不合格} | 载体 = 字段级 `value_counts` |
| `test_真实附件6_三类dtype齐备`（新） | 附件6 37 字段 | 按 role 分组：利润定额={int}、实际利润={float}、评级={str} | 三层表头展开后的类型正确性 |
| `test_真实附件4_月份列为整数`（新） | 附件4 12 个月列 | 12 条且 dtype == {int} | — |

### 2.3 FR-5（变更 5 条）

| 用例 | 输入 | 预期 | 状态 |
|---|---|---|---|
| `test_guard_多处违规全部上报` | `import os` + `os.system` + `import socket` + `eval` | violations == **4**（白名单下 `import os` 亦违规），覆盖 import / 属性调用 / 裸名调用三类 | **修正**（原断言 3，基于旧「import os 合法」前提） |
| `test_guard_危险顶层模块被白名单拒绝` | `import os` / `os.makedirs` / `import sys` / `import importlib` / `import shutil` | 逐条抛 `ScriptGuardError` | **取代**原 `test_guard_允许import_os等本地标准库` |
| `test_guard_拦截from_os_import_system绕过写法` | `from os import system; system('dir')` | 抛 `ScriptGuardError` | 摘 `strict=True` xfail，**已绿** |
| `test_guard_拦截importlib动态导入绕过写法` | `importlib.import_module('subprocess')` | 抛 `ScriptGuardError` | 摘 xfail，**已绿** |
| `test_guard_拦截第三方HTTP库httpx` / `test_guard_拦截标准库http_client` | `import httpx` / `import http.client` | 抛 `ScriptGuardError` | 摘 xfail，**已绿**（XPASS → 转正） |

### 2.4 数据核查（变更 2 条）

| 用例 | 输入 | 预期 | 状态 |
|---|---|---|---|
| `test_缺失值不应被判为越界值` | 得分=[50, NaN, 120] | 越界行号仅 [2] | 摘 xfail，**已绿** |
| `test_全空数值列不触发越界_缺失由not_null规则单独管` | 得分=[NaN, NaN] | range 规则无 violation、`passed=True`；加 `not_null` 规则后 rows=[0,1] | **取代**原「锁定错误现状」的用例，改为断言正确行为 + 缺失归口 |

---

## 3. 运行结果（本地真实输出）

```
H:\mm-agent> .venv\Scripts\python.exe -m pytest tests -q
........................................................................ [ 37%]
........................................................................ [ 75%]
...................x..........................                           [100%]
189 passed, 1 xfailed in 43.73s            # RUN#2 RUN_START 20:39:34 / RUN_END 20:40:21（终版改名后复核）
```

> RUN#1（20:34:20–20:35:12，`48.62s`）与 RUN#2（20:39:34–20:40:21，`43.73s`）同结果，
> 两次之间仅发生 `table_id` 改名（`att5_complaint` / `att6_profit`），无断言变更。

- `0 skipped`：真实数据目录 `C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题` 存在，
  6 个 `.xls` 附件的**集成用例实际执行**（未被 skipif 跳过）。
- `1 xfailed`：`tests/test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕` —— 在本地真实执行并失败
  （`xfail` 而非 `skip`），即待补项已复现取证。
- 稳定性：全量套件多次运行结果一致（耗时随机器负载 48s–136s 波动）；8 线程并发写用例连跑 3 次全绿。

---

## 4. 未覆盖项（明确列出，避免误读为「已验完」）

| # | 未覆盖内容 | 原因 |
|---|---|---|
| 1 | `mmagent/app.py`、`mmagent/cli.py`、`mmagent/tools/`、`mmagent/api/`、`mmagent/rag/` | 按任务书本轮**未冻结**，不写用例 |
| 2 | `mmagent/agents/*`、`mmagent/pipeline/intake_llm.py`、`mmagent/pipeline/repair.py` | 依赖本地 Ollama 与提示词渲染，属非确定性链路；建议后续用 mock LLM 做契约测试 |
| 3 | FR-5 runner 在 Windows 上「超时子进程是否留下孤儿进程」 | 只断言 `timed_out`/`rc=-1`/`duration_s<20`；未查 `tasklist` 残留（测试环境不宜强依赖） |
| 4 | FR-9 `write_version` 的 `OperationalError → 退避重试 3 次` 路径 | 本机无法稳定构造 `database is locked`；已以 8 线程并发写覆盖「不跳号」主目标 |
| 5 | NFR-7 脱敏（`desensitize_hit` 表 + `config/desensitize.yaml`） | 不在本轮必写清单；`security/desensitize.py` 本轮无用例 |
| 6 | 附件2/3 评级列、附件6 双层表头的**语义映射逐列人工核对** | 本轮已断言结构、canonical、期号、dtype、`value_counts` 分布；「列义是否映射正确」仍需 Archivist 人工确认（field_mapping.csv 的 `confirmed_by` 即为此设计，当前留空） |
| 7 | 真实附件中 `.xlsx`（`总表.xlsx` 的解析已由 `test_真实目录_白名单跳过两个预处理文件` 间接覆盖，但 `总表` 的 12 个月列语义未逐列断言） | 集成夹具固定「6 个 .xls」，以稳定「6 张附件」断言；`总表` 仅断言入账与留痕 |
| 8 | 附件1 门店级 golden（A1 年均 99.7833 / B14 90.3250 / D7 81.8500） | 属**行级**均值，`schema_report.json` 无行级数据载体（`numeric_sum` 是列合计）；已以「附件4/5/6 表级合计」4 个 golden 替代覆盖求和正确性。若后续产物提供行级/门店级摘要，可立即接上 |

---

## 5. ISSUES（源码缺陷与过程问题；Tess 未修改任何源码）

### ✅ 已修复（第一段发现，第二段复测转绿）

#### ISSUE-FR9-01（原 中高，不可变保证可绕过）`INSERT OR REPLACE` 可原地覆盖 adopted 行 — **已修复**
- 原现象：裸连接执行 `INSERT OR REPLACE INTO profile_doc (…已 adopted 的 version_id…)` **静默成功**，
  回读该行 `patch` 变 `{"hacked":1}`、`actor` 变 `attacker`。
- 根因（已用测试独立佐证）：`schema.sql` 原有 `BEFORE UPDATE` / `BEFORE DELETE` 两个触发器，
  而 SQLite 的 `INSERT OR REPLACE` 走**隐式 DELETE**，该隐式 DELETE 仅在 `PRAGMA recursive_triggers=ON`
  （默认 OFF）时才触发 → 绕过。佐证用例：`test_开启recursive_triggers后_REPLACE被现有触发器拦下`
  （**第一段就已绿**）——同一 REPLACE 在 `recursive_triggers=ON` 下被现有触发器 ABORT 并抛 `adopted immutable`。
- 修复：① `schema.sql` 新增 `trg_profile_adopted_guard_insert`（`BEFORE INSERT`，
  当 `(NEW.profile_id, NEW.version_id)` 已存在且旧行 `status='adopted'` 时 `RAISE(ABORT,'adopted immutable')`）；
  ② `db.connect()` 连接级补 `PRAGMA recursive_triggers=ON`（双保险：让隐式 DELETE 也触发原 BEFORE DELETE）。
- 复测：`test_INSERT_OR_REPLACE_不能覆盖adopted行`、`test_INSERT_OR_REPLACE_失败后adopted行数据未被改动`
  **均已绿**（原为 xfail），且后者以 `SELECT *` 回读做**字节级**比对，非仅断言异常类型。

#### ISSUE-FR5-01 / FR5-02 / FR5-03（原 中，安全绕过）guard 黑名单可绕过 — **已修复**
- 原现象：`from os import system; system('dir')`、`importlib.import_module('subprocess')`、
  `import httpx`、`import http.client` 四类写法全部**放行**（httpx 已装在 venv 内，与「全本地零外网」冲突）。
- 修复：`guard.py` 由「黑名单」改为 **import 白名单（14 个本地模块）+ 危险调用黑名单**，
  并保留 `ImportFrom` 根名校验与属性调用校验。白名单模型下 `os`/`sys`/`importlib`/`httpx`/`http` 一律拒绝。
- 复测：4 条绕过用例 + 新增 `test_guard_危险顶层模块被白名单拒绝` 全部绿；
  `test_guard_白名单化后常用本地库仍须放行`（csv/json/math/re/collections/datetime/pathlib/statistics/
  numpy/pandas/matplotlib）继续绿 —— 证明白名单**没有误伤**正常建模/绘图脚本。

#### ISSUE-SANITY-01（原 低，误报）缺失值被并入「越界值」 — **已修复**
- 原现象：`{"得分": [50, NaN, 120]}` → 越界行号 `[1,2]`（第 1 行其实是空值）；
  全空列被整体判为「全部越界」。
- 修复：`_check_range` 改为 `notna` 与比较式**显式与**（`bad = notna & (col < vmin) | notna & (col > vmax)`）。
- 复测：`test_缺失值不应被判为越界值`（原 xfail）绿；
  新 `test_全空数值列不触发越界_缺失由not_null规则单独管` 绿 —— 缺失归 `not_null` 规则单独负责，职责分离。

#### ISSUE-FR2-02（原 低，口径误标）门店标识列被标成月度周期 — **已修复**
- 原现象：`store_id` 列 `period == "月"`（`_period_of` 用「月」做子串匹配命中「**月**份门店」）。
- 复测：`test_门店标识列不带周期语义`（原 xfail）绿 —— `period == ""` 且 `canonical == "store_id"`。

#### ISSUE-PERF-01（原 中，性能）每连接重复设置 WAL — **已修复**
- 原现象：`db.connect()` 每次执行 `PRAGMA journal_mode=WAL`，实测单次 connect ≈100ms（最坏 ~225ms），
  而 `journal_mode` 是**库级持久属性**，等于把「一次建库动作」摊到每个 DB 往返上。
- 修复：WAL 移入 `init_db()`（建库路径执行一次）；`connect()` 只保留连接级 `foreign_keys`/`busy_timeout`
  （+ 新增 `recursive_triggers`）。文档字符串亦更新说明。
- 复测：`test_连接仍保持WAL与外键约束` 绿（`journal_mode==wal`、`foreign_keys==1`、`busy_timeout==5000`）——
  防止「把 WAL 移走时顺手丢了库级设置」。
- **测试策略说明（保留）**：我**故意不写计时断言**。曾加 `10 次 connect < 1.5s`，
  同环境有时 ~1.2s XPASS、有时 ~3.9s FAIL —— 计时用例在共享机器上本质 flaky，会污染 CI 信号。
  性能项以 ISSUE 形式交实现方/压测判定，不用单测兜。

#### ISSUE-PROC-01（原 中，流程）「已冻结」模块在测试期间被反复改动 — **已缓解**
- 原现象：第一段期间 `intake.py`/`schema.sql`/`runner.py` 在测试进行中多次改动，FR-2 用例被打破 3 次。
- 处理：① 已按 §0 修订复测全绿；② 「必需键/必需列」一律改**子集/键名断言**——
  实现新增键不会误伤，删键或改语义仍会红；③ 队长已发布 md5 基线制度，本报告 §0 即最终冻结表。
- 残留建议：**冻结 → 发布 md5 → 改动触达已测模块必须先通知测试重跑**，禁止在未复跑情况下宣布完成。

### ⬜ 仍未修复（保留 1 条 xfail）

#### ISSUE-FR9-03（低，审计链缺环）REPLACE 违规守卫无事件留痕 — 对应 1 条 xfail
- 证据：`tests/test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕`。
  触发 REPLACE 违规后，`events` 表中**没有** `error_code='ADOPTED_IMMUTABLE'` 记录（该用例当前 xfail）。
- 定位：`mmagent/store/schema.sql:37-45` 的守卫是 SQL 触发器，`RAISE(ABORT, 'adopted immutable')`
  **直接回滚语句**，`profile.py:41-45` 的异常→事件转换只在**应用层** `try_update_row` 路径上生效；
  裸 SQL 路径（含未来 LLM 生成脚本）绕过应用层时不会留痕。
- 影响：不可变约束**已不可被绕过**（数据安全已保住），但「谁在什么时候尝试过篡改 adopted 版本」
  在裸 SQL 路径下无审计记录 —— 属审计完整性补完项，非安全漏洞。
- 建议：在写路径统一捕获 `sqlite3.IntegrityError`（消息含 `adopted immutable`）后补记事件；
  或增加一个「违规尝试」台账表由触发器写入（SQLite 触发器内 `INSERT` 到独立表是可行的）。
- 说明：按队长口径「若实现方未修则保持 xfail 并注明」，本用例保持 `xfail(strict=False)`。

### 观察项（不构成缺陷，供队长 / Vic 参考）

| # | 内容 | 证据/定位 |
|---|---|---|
| O-01 | `ScriptGuardError.violations` 顺序为 `ast.walk` 遍历顺序而非源码行号顺序（出现「第3行」排在「第2行」之前） | `guard.py` `ast.walk(tree)`；`test_guard_多处违规全部上报` 已改为集合/包含式断言以不受顺序影响 |
| O-02 | `table_id` 已由 `T01..T06` 改为**语义化名**（终版：`att1_satisfaction`/`att2_service`/`att3_clean`/`att4_complaint`/`att5_complaint`/`att6_profit`/`summary_master`），但 `summary_master` 被**两个 sheet 共用**（`总表.xlsx` 的 `A题 (2)` 与 `A题`）——同 table_id 冲突记录里无法区分 sheet | `intake.py`；本文件用例已按 `source_file` 前缀 / `(source_file, sheet_name)` 定位、且**不硬编码 table_id**（`grep` 实测 0 处），故改名不受影响 |
| O-03 | `intake_summary.md` 的「缺失率 Top10」小节标题括号后缀会随数据切换（`（raw_missing_rate）` vs `（post-fillna 口径）`） | 合成数据下为 `## 缺失率 Top10（post-fillna 口径）`，真实全目录下为 `## 缺失率 Top10（raw_missing_rate）`；测试只断言稳定前缀 |
| O-04 | `dtype == 'datetime'` 在**字段记录**里实际不可达：取值纪律只收「门店列 + 月份组列」，日期列一律进 `unmapped_columns`；月份列取值通常为数值 | `intake.py:397-435`；第一段的 `test_日期型字段被识别为datetime` 属**测试假设错误**（已改为断言真实契约 `test_非月度列不入字段表而记入未映射留痕`）。若后续要支持日期列入库，需先扩展取列规则 |
| O-05 | `numeric_sum` 只对「有数值样本的月份列」建键：附件5 的 `1月`/`4月` 因整列空格（按 0 计）未出现在 `numeric_sum` 里，但合计仍正确（131.0） | `intake.py` 表级聚合；聚合断言用 `sum(numeric_sum.values())` 对键名不敏感，避免误伤 |

---

## 6. 本次交付物与复跑方式

```
H:\mm-agent\tests\
├── conftest.py                  # sys.path 引导 + tmp_db 夹具 + 真实附件夹具（缺失则 skip）
├── test_fr9_profile.py          # FR-9 档案版本链（32 例）
├── test_fr2_intake.py           # FR-2 附件体检（51 例）
├── test_fr5_guard_runner.py     # FR-5 守卫 + 沙箱执行（43 例）
├── test_sanity.py               # 数据核查（24 例）
├── test_fr6_render.py           # FR-6 出图（19 例）
└── test_fr13_disclosure.py      # FR-13 合规声明（21 例）
```

> `tests/test_fr2_intake_rework.py`（第一段的 17 条「重构后行为预备用例」）已在第二段**删除**：
> 其覆盖内容已全部并入 `test_fr2_intake.py` 并**转正为绿**，保留会形成两套口径。

复跑：
```bat
cd H:\mm-agent
.venv\Scripts\python.exe -m pytest tests -q                  :: 全量（190 例）
.venv\Scripts\python.exe -m pytest tests -q -rxX             :: 附带 xfail 原因 / XPASS 清单
.venv\Scripts\python.exe -m pytest tests -q --junitxml=.agent-runs\2026-10-13\task-001\junit-final.xml
```

冻结基线比对（若任一 md5 变动，结论即失效，须重跑）：
```bat
cd H:\mm-agent
.venv\Scripts\python.exe -c "import hashlib,pathlib,sys; root=pathlib.Path(r'H:\mm-agent'); ts=sorted(root.glob('mmagent/**/*.py'))+sorted(root.glob('config/*.yaml'))+sorted(root.glob('mmagent/store/*.sql')); [print(p.relative_to(root).as_posix(), hashlib.md5(p.read_bytes()).hexdigest()) for p in ts]"
:: 与 §0 表逐行比对；机器可读副本：.agent-runs/2026-10-13/task-001/md5-baseline-final.md
```

环境提示：本机沙箱在 pytest 回收临时目录时会打印
`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]`，属环境提示；不影响用例结论
（用例本身不会删除测试目录之外的文件）。

---

## 7. xfail 现状与 golden 覆盖小结

**xfail 现状（1 条）**

| 用例 | 期望 | 状态 |
|---|---|---|
| `test_fr9_profile.py::test_INSERT_OR_REPLACE_违规留下事件留痕` | 裸 SQL REPLACE 违规也写入 `events`（error_code=ADOPTED_IMMUTABLE） | 仍 xfail，见 ISSUE-FR9-03 |

**第一段各 xfail 组的去向（已逐条核对，无遗漏、无「静默跳过」）**

| 第一段 xfail 组 | 条数 | 去向 |
|---|---|---|
| FR-2 重构预备用例（`test_fr2_intake_rework.py`） | 17 | **全部并入 `test_fr2_intake.py` 并转正绿**；过期文件已删除 |
| FR-9 `INSERT OR REPLACE` 绕过 | 3 | 2 条转正绿；1 条（事件留痕）保留 xfail |
| FR-5 guard 绕过（from-os-import / importlib / httpx / http.client） | 4 | **全部转正绿** |
| sanity NaN 误报（缺失值 / 全空列） | 2 | 均转正绿（后者改写为「缺失归 not_null 管」的正确行为断言） |
| FR-2 门店列 period 误标 | 1 | **转正绿**（见 ISSUE-FR2-02） |

**golden 数值现已全部接上（第一段「无载体」问题已解决）**

| golden | 真值 | 载体 | 断言用例 |
|---|---|---|---|
| 附件4 年度合计 | 432.0 | 表级 `numeric_sum` | `test_真实附件_数值合计为冻结golden` |
| 附件5 年度合计 | 131.0 | 表级 `numeric_sum` | 同上 |
| 附件6 全年定额 | 27289.0 | 表级 `numeric_sum`（键含「利润定额」） | 同上 |
| 附件6 实际利润 | 31344.0 | 表级 `numeric_sum`（键含「实际利润」） | 同上 |
| 附件6 合计 | 58633.0 | 表级 `numeric_sum` 全量 | 同上 |
| 附件2 M12 评级分布 | {合格:144, 不合格:13} | 字段级 `value_counts` | `test_真实附件_12月评级分布为冻结golden` |
| 附件3 M12 评级分布 | {合格:154, 不合格:3} | 字段级 `value_counts` | 同上 |
| 附件1/6 三口径行数 | 160/157/3；157/157/0 | 表级 `physical_rows`/`effective_rows`/`blank_rows` | `test_真实附件_三口径行数为冻结真值` |
| 6 附件字段数 | 13/25/25/13/13/37 | 表级 `field_count` | `test_真实附件_字段数为冻结真值` |
| 未映射/脏值留痕 | 附件1 N 列 1 条（derived）；'97.3.4' 17 处 | `unmapped_columns`/`dirty_cells` | `test_真实附件_留痕清单结构完整`、`test_真实目录_白名单跳过两个预处理文件`（全目录 unmapped=2） |

> 唯一未直接断言的 golden 是**行级**指标（附件1 A1 年均 99.7833 等），
> 因 `schema_report.json` 无行级/门店级载体（详见 §4 第 8 条）。属**刻意不接**，非遗漏。
