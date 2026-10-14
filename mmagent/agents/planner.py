"""planner 角色：建模规划（问题拆解、方法选型、任务分解）。薄封装骨架。"""
from __future__ import annotations

from .base import RoleRunner

PLANNER_PROMPT_VERSION = "planner@0.1.0"

PLANNER_SCHEMA = {"required": ["tasks"]}


def make_runner(**kwargs) -> RoleRunner:
    return RoleRunner("planner", PLANNER_PROMPT_VERSION, PLANNER_SCHEMA, **kwargs)
