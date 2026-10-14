# task-001 场景一数模竞赛协作Agent — 2小时冲刺开发计划

- 日期：2026-10-13 18:35 起，限时 2 小时（约 20:35 收尾记录进度）
- 依据：《场景一-数模竞赛协作Agent-实施规划书 V1.1》《项目开发计划书 V1.1》+ 课件 C:\Users\Lenovo\Desktop\课件\agent
- 仓库根：H:\mm-agent（MM_ROOT）；数据根：C:\Users\Lenovo\Desktop\数学建模\2024 数学建模（校赛）A题

## 环境实测（Phase 0 已核）
- Ollama 在线 localhost:11434；可用模型：qwen3.5:latest、qwen3.5:0.8b、qwen3-embedding、deepseek-r1:8b、gemma3 等
  - ⚠️ 偏差：无 qwen3:4b → 降级链改为 qwen3.5 → qwen3.5:0.8b
- ⚠️ 偏差：本机无 CPython 3.11 → 使用 3.13.12（托管运行时），记入 KNOWN_ISSUES
- 数据目录在位：附件1~6（.xls 旧格式）+ 题面/论文 docx 齐全

## 本次冲刺范围（2 小时可交付）
1. 仓库骨架（mmagent 包 + config + scripts + tests + artifacts 目录，依赖单向规则）
2. P0-4 FR-9 档案版本链：store/schema.sql + db.py + profile.py（write_version / adopt / AdoptedVersionImmutableError / 草案裁剪 5 版 / 触发器）
3. P0-1 FR-2 附件体检（确定性部分）：pipeline/intake.py run_intake_audit → 4 件产出
4. P0-2 FR-5 确定性件：guard.py(validate_script_ast) + runner.py(run_script) + checks/sanity.py + repair.py 骨架
5. P0-3 FR-6：plotstyle.py + pipeline/render.py（dpi300、缺字形检测）
6. P0-5 FR-13：compliance/disclosure.py make_ai_disclosure（md+docx）
7. agents/base.py（Ollama 客户端、JSON 强制、schema 校验、重试/降级、prompt 版本记录）+ coordinator 状态机骨架
8. FastAPI 最小骨架 + cli 入口
9. 测试：FR-9 / FR-2 / guard / sanity / disclosure 单测，pytest 跑绿
10. 收尾：进度记录 PROGRESS.md + final-summary.md，便于下次续作

## 阶段调度
- Phase 1 Vera：调查课件代码可复用模式 + 数据附件格式实测（只读）
- Phase 2 Max：按上述范围实现（allowed_edit_scope = H:\mm-agent 内，除 .agent-runs 与 tests）
- Phase 3 Tess：编写 tests/
- Phase 4 Vic：创建 venv、装依赖、跑 pytest 出 PASS/FAIL
- 收尾：队长汇总 final-summary + PROGRESS.md
