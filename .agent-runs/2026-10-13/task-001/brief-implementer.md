# 任务书：实现工程师 Max（Phase 2）— 场景一数模协作Agent 仓库实现

- role_name: 实现工程师 Max
- mission: 在 H:\mm-agent 下按《实施规划书》实现仓库骨架 + P0 确定性核心 + agents/base + coordinator 骨架 + FastAPI 最小骨架 + CLI。
- allowed_edit_scope: H:\mm-agent\mmagent\、H:\mm-agent\config\、H:\mm-agent\requirements.txt、H:\mm-agent\README.md。**禁止**改 tests\、.agent-runs\、.venv\。
- output_path: H:\mm-agent\.agent-runs\2026-10-13\task-001\implementer-report.md
- Python 环境：H:\mm-agent\.venv\Scripts\python.exe（依赖已装：fastapi/uvicorn/pandas/openpyxl/xlrd/matplotlib/python-docx/pydantic/pyyaml/httpx/pytest/jinja2）

## 硬约束（来自规划书，必须遵守）
1. 全本地：任何模块不得 import requests/socket/urllib 直连外网；Ollama 只允许 http://localhost:11434（用 httpx，base_url 从 config/models.yaml 读，禁止硬编码模型名）。
2. 依赖方向单向：api → agents → tools → pipeline → store → db；store/ 与 checks/ 不得 import agents/ 或 api/。
3. 所有时间戳 TEXT ISO8601 UTC；SQLite 开 WAL，连接级 PRAGMA 见下。
4. 中文注释与文档字符串，代码风格简洁，核心源码控制规模。

## 仓库骨架（mmagent 包）
```
mmagent/
  __init__.py
  app.py                # create_app() FastAPI 工厂
  cli.py                # python -m mmagent.cli 入口（argparse 子命令）
  plotstyle.py          # apply_paper_style()：中文字体(宋体/黑体 fallback)、dpi300、负号正常显示
  store/
    db.py               # 连接工厂：PRAGMA journal_mode=WAL; foreign_keys=ON; busy_timeout=5000
    schema.sql          # 见下
    profile.py          # FR-9 见下
  pipeline/
    intake.py           # FR-2 见下
    intake_llm.py       # explain_conflicts(conflicts, budget_tokens=2000) 骨架，调 agents.base
    guard.py            # validate_script_ast(src) 见下
    runner.py           # run_script(script_path, workdir, timeout_s=300) 见下
    repair.py           # repair_once(run_id, stderr_tail, prev_src) 骨架（本轮可只留接口+TODO）
    render.py           # FR-6 见下
  checks/
    sanity.py           # run_sanity_checks(result_tables, rules) -> SanityReport 见下
  compliance/
    disclosure.py       # FR-13 见下
  agents/
    base.py             # 见下
    coordinator.py      # 规则状态机骨架（阶段枚举 + 转移函数 + 事件表写入，interrupt 点留 TODO）
    archivist.py / planner.py / coder.py / assembler.py   # 4 个角色的 prompt 版本常量 + 调 base 的薄封装（可骨架）
  tools/
    intake_tool.py / solve_tool.py / plot_tool.py / profile_tool.py / compliance_tool.py  # 薄封装，纯确定性
  api/
    routes_intake.py / routes_solve.py / routes_profile.py / routes_compliance.py  # 最小路由
  rag/
    adapter.py          # 占位，注释说明唯一允许 import <RAG_ROOT> 的文件
config/
  models.yaml           # ollama base_url、生成模型 qwen3.5:latest、降级 qwen3.5:0.8b、embedding qwen3-embedding、各角色温度/token预算/调用预算
requirements.txt        # 锁定本次所装版本（pip freeze 精简手写）
README.md               # 快速开始
```

## P0-4 FR-9 档案版本链（store/schema.sql + store/profile.py）— 最高优先级
- 表 profile_doc：profile_id TEXT、version_id INTEGER、status TEXT CHECK(status IN ('draft','adopted','superseded'))、patch TEXT(JSON)、actor TEXT、upstream_refs TEXT(JSON)、supersedes INTEGER NULL、created_at TEXT，主键 (profile_id, version_id)。
- 触发器 trg_profile_adopted_immutable：BEFORE UPDATE ON profile_doc WHEN OLD.status='adopted' → RAISE(ABORT,'adopted immutable')；DELETE adopted 同样禁止。
- 事件表 events：id、ts、event_type、actor、payload TEXT(JSON)、trace_id。
- artifacts 表：id、ts、kind、path、profile_id、version_id、producer、prompt_version、model_tag、input_hash。
- metric_obs 表：id、ts、metric、value、unit、run_id、prompt_version、model_tag。
- profile.py：
  - `write_version(profile_id, patch, actor, upstream_refs) -> version_id`：单事务 BEGIN IMMEDIATE 内 SELECT MAX(version_id)+1；写入 status='draft'；写事件表；写完后草案裁剪：DELETE draft 且 version_id 不在最新 5 个 draft 内；失败重试 3 次退避 100ms。
  - `adopt_new_version(profile_id, from_version_id, actor)`：旧 adopted → 只 INSERT 新行（supersedes=from_version_id，先 draft 再置 adopted 通过两次 INSERT 语义或同事务 UPDATE draft→adopted），永不 UPDATE adopted 行。
  - `read_profile(profile_id, version='last', paths=None) -> dict`。
  - 自定义异常 AdoptedVersionImmutableError：捕获触发器 IntegrityError 转换，并写事件表 error_code='ADOPTED_IMMUTABLE'。

## P0-1 FR-2 附件体检（pipeline/intake.py）
- `run_intake_audit(dataset_dir, out_dir) -> dict`：遍历目录下 .xls/.xlsx（xlrd 读 .xls，openpyxl 读 .xlsx），对每表每字段产出：table_id / sheet_name / field_raw / field_norm(去空格小写) / dtype / row_count / missing_rate / merge_cells / sample_values(≤5)。
- 产出 4 个文件到 out_dir：schema_report.json、caliber_conflicts.json（启发式检测 unit/period/entity_scope/definition 四类，字段名含单位括号、同名字段跨表 dtype 不一致等，本轮启发式即可）、field_mapping.csv（列：table_id,field_raw,field_norm,unit,period,entity_scope,source_file,source_sheet,confirmed_by,confirmed_at）、intake_summary.md（每表行数、缺失率 Top10、冲突清单）。

## P0-2 FR-5 确定性件
- guard.py `validate_script_ast(src) -> None`（违规抛 ScriptGuardError）：ast 解析，禁 import/调用 os.system、subprocess、eval、exec、__import__、open 写模式到白名单外路径（本轮：禁 requests/socket/urllib/ftplib import，禁 os.system/subprocess/eval/exec/__import__/compile 调用，禁 while True 死循环检测可略）。
- runner.py `run_script(script_path, workdir, timeout_s=300) -> dict`：subprocess.run([sys.executable, script_path], cwd=workdir, capture_output=True, timeout=timeout_s)，返回 {returncode, stdout_tail, stderr_tail, duration_s, produced_files}；成功=returncode==0 且 manifest.json（若有）声明文件齐全。
- checks/sanity.py `run_sanity_checks(result_tables: dict[str, DataFrame], rules: list[dict]) -> SanityReport`：内置规则：得分 0~100、占比 0~1、利润 ≥0、行数一致、主键唯一；SanityReport(passed: bool, violations: list[{rule, table, column, rows, message}], blocking: bool)。

## P0-3 FR-6 出图（plotstyle.py + pipeline/render.py）
- plotstyle.apply_paper_style()：matplotlib rcParams 中文字体（SimSun/Microsoft YaHei fallback 列表）、axes.unicode_minus=False、figure.dpi=300、savefig.dpi=300。
- render.py `render_figures(result_tables, figure_specs, out_dir) -> FigureManifest`：按 figure_specs（kind: line/bar/scatter/hist，x,y,title...）出 PNG，写 figure_manifest.csv（figure_id,path,kind,source_table,dpi）；返回 manifest 对象；检测 PNG dpi 元数据=300。

## P0-5 FR-13 合规声明（compliance/disclosure.py）
- `make_ai_disclosure(profile_id, version_id, output_dir) -> Path`：生成 ai_disclosure.md 与 ai_disclosure.docx（python-docx），内容含四字段：Agent 参与范围、人改比例、模型与提示词版本（从 artifacts/metric_obs 可查的写可查，查不到写占位）、数据与素材来源声明；人改比例由 Python 计算（本轮可传参或由 profile patch 统计，给合理默认+注释）。

## agents/base.py（LLM 调用基座）
- OllamaClient：httpx.Client(base_url 从 config/models.yaml 读，默认 http://localhost:11434)，chat(model, messages, temperature, format='json', timeout) → 返回 content 字符串。
- RoleRunner：每个角色给定 prompt_version（<role>@x.y.z）、system 模板路径 prompts/<role>.system.j2（jinja2 渲染）、输出 JSON schema（dict）；强制：输出必须是单个 JSON 对象，json.loads 失败 → 重试 1 次（temperature=0.0）→ 再失败降级到 fallback 模型（models.yaml）；schema 校验（手写轻量 required 校验即可，不引 jsonschema 库也行）；每次调用写 artifacts 与 metric_obs（prompt_version/model_tag/input_hash/latency_ms）。
- coder 角色允许 Markdown 代码围栏，base 提供 strip_code_fence()。
- coordinator.py：Stage 枚举（INTAKE→PROFILE→PLAN→SOLVE→RENDER→ASSEMBLE→EXPORT）、Transition 表、Coordinator.step(state) 纯 Python 路由、三个人工卡点（口径冲突/ sanity blocking / 导出前合规）留 interrupt 点（raise HumanGateRequired 异常占位，注释说明对应 LangGraph interrupt()），失败计数与「连续失败 2 次转人工」规则。

## API + CLI（最小可用）
- app.py create_app()：挂 /api/v1/intake/run（POST，调 intake_tool）、/api/v1/profile/{id}（GET）、/api/v1/compliance/disclosure（POST）、/api/v1/health（GET）；统一错误体 {error_code,message,detail,trace_id}。
- cli.py 子命令：db init --db data\mm.db；intake --dataset <dir> --out artifacts\intake；env-attest --out artifacts\reports\env_attest.json（记录 python 版本、依赖哈希、ollama 模型列表）；netguard-check（静态扫描仓库 .py 无 requests/socket/urllib import，排除本扫描器自身与注释说明）。

## 完成后
写 implementer-report.md（FINAL STATUS / CHANGED_FILES / 各模块要点 / 未完成项），消息回传队长状态块。不要跑 pytest（Phase 3/4 的事），但可以用 .venv python 做 import 冒烟（python -c "import mmagent..."）。
