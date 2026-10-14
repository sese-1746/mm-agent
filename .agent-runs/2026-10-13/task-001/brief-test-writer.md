# 任务书：测试工程师 Tess（Phase 3）— 场景一测试用例

- role_name: 测试工程师 Tess
- mission: 为 H:\mm-agent 的 P0 模块编写 pytest 测试，只写 tests/ 目录。
- allowed_edit_scope: 仅 H:\mm-agent\tests\（含 conftest.py、fixtures、golden 期望件）。**禁止**改 mmagent/ 任何源码——发现源码 bug 写进测试报告的 ISSUES 章节。
- output_path: H:\mm-agent\.agent-runs\2026-10-13\task-001\test-report.md
- Python：H:\mm-agent\.venv\Scripts\python.exe；运行方式：cd H:\mm-agent && .venv\Scripts\python.exe -m pytest tests -q

## 前置阅读
1. 任务书 brief-implementer.md（同目录）了解模块签名与语义
2. Max 的实现报告 implementer-report.md（若存在）与 mmagent/ 源码实际签名——以源码为准

## 必写用例（对应 P0 验收口径）

### tests/test_fr9_profile.py（FR-9 核心，最多用例）
- 临时库（tmp_path 下 mm.db，store.db 连接 + schema.sql 建表）
- 连续 write_version 写 8 次：断言 draft 版本恰好 5 个（草案裁剪）且 version_id 严格递增
- adopt_new_version 后：旧版不被 UPDATE（supersedes 链可查）；对已 adopted 版本尝试直接 SQL UPDATE → 触发器抛错 → 应用层 AdoptedVersionImmutableError，且事件表有 error_code='ADOPTED_IMMUTABLE' 记录
- read_profile(version='last') 返回最新 adopted 或 draft（以实现语义为准）
- patch/upstream_refs JSON 字段往返一致

### tests/test_fr2_intake.py（FR-2）
- 用迷你合成数据：tmp_path 下造 2 个 .xls（xlwt 不可用就造 .xlsx 用 openpyxl，另测真实附件抽 1 个字段）——更好做法：直接用真实数据目录 C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题 做集成测试（只读），断言：6 张附件各产出 ≥1 条表级记录；4 个产出文件（schema_report.json / caliber_conflicts.json / field_mapping.csv / intake_summary.md）全部存在且非空；schema_report 中每字段含 table_id/field_raw/dtype/row_count/missing_rate 键；missing_rate ∈ [0,1]
- 若真实目录不存在则 skip（pytest.mark.skipif）

### tests/test_fr5_guard_runner.py
- validate_script_ast：含 os.system / subprocess / eval / exec / import requests / import socket 的源码必须抛 ScriptGuardError；干净源码（pandas 计算 + matplotlib 出图）通过
- run_script：写一个打印并生成结果文件的小脚本到 tmp_path，断言 returncode==0、produced_files 非空；再跑一个必崩脚本断言 returncode!=0 且 stderr_tail 非空；超时用 timeout_s=2 跑 sleep 脚本断言超时处理

### tests/test_sanity.py
- 得分 0~100 越界、占比 >1、利润 <0、主键重复各造一个 DataFrame，断言 violations 命中且 blocking=True；干净数据 passed=True

### tests/test_fr6_render.py
- render_figures 出 1 张折线 PNG：断言文件存在、PIL 或 matplotlib 读 dpi 元数据为 300（PIL 不可用则用 png 字节解析或以实现提供的校验函数为准）、manifest csv 行数正确

### tests/test_fr13_disclosure.py
- make_ai_disclosure 生成 md+docx 双文件、非空；md 内容含「Agent 参与范围」「人改比例」「模型」「数据来源」四个关键字段

## 纪律
- 不断言实现细节之外的私有函数；公开签名以源码为准
- 每个测试文件头部注释标注对应 FR 编号
- 全部跑绿后写 test-report.md（用例清单 / 覆盖的 FR / 发现的 ISSUES /  pytest 输出摘要），消息回传队长
