"""assembler 角色：论文装配（只做结构，不写正文）。

**本轮（task-008 / 第 21 轮）未接线**：docx 装配由
`mmagent/tools/paper_tool.py::build_paper` 以**纯确定性**方式完成
（README §0-R1 裁定：§8.4 既要求「不得撰写摘要与论证段落」，正文就没有需要
模型生成的内容，故不调 LLM）。本文件只保留角色定义（提示词 + schema），
**不得**为了「有实现」而伪造一次调用。

偏差登记：D-43（装配确定性化 + assembler 未接线）。`docs/**` 本轮冻结，本行只作
代码内引用；完整登记见 `.agent-runs/2026-10-18/task-008/implementer-report.md`。
"""
from __future__ import annotations

from .base import RoleRunner

ASSEMBLER_PROMPT_VERSION = "assembler@0.2.0"

ASSEMBLER_SCHEMA = {"required": ["sections"]}


def make_runner(**kwargs) -> RoleRunner:
    return RoleRunner("assembler", ASSEMBLER_PROMPT_VERSION,
                      ASSEMBLER_SCHEMA, **kwargs)
