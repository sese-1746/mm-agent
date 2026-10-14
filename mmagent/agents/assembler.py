"""assembler 角色：论文组装（章节合并、图表引用、格式统一）。薄封装骨架。"""
from __future__ import annotations

from .base import RoleRunner

ASSEMBLER_PROMPT_VERSION = "assembler@0.1.0"

ASSEMBLER_SCHEMA = {"required": ["sections"]}


def make_runner(**kwargs) -> RoleRunner:
    return RoleRunner("assembler", ASSEMBLER_PROMPT_VERSION,
                      ASSEMBLER_SCHEMA, **kwargs)
