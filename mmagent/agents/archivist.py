"""archivist 角色：档案官（体检解释、口径冲突说明、档案整理）。薄封装骨架。"""
from __future__ import annotations

from .base import RoleRunner

ARCHIVIST_PROMPT_VERSION = "archivist@0.1.0"

# 输出 JSON 的最小约束（骨架期只约束顶层键）
ARCHIVIST_SCHEMA = {"required": ["summary"]}


def make_runner(**kwargs) -> RoleRunner:
    return RoleRunner("archivist", ARCHIVIST_PROMPT_VERSION,
                      ARCHIVIST_SCHEMA, **kwargs)
