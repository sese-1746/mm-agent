"""coder 角色（FR-5）：代码与出图脚本生成。

`generate_script(task_spec, prev_error=None) -> dict` 走 agents/base 的 RoleRunner
（prompt_version = coder@1.0.0），输出 JSON schema 见 prompts/coder.system.j2：
module / filename / purpose / declared_outputs / code / figure_specs。
生成后**必须**过 pipeline.guard：违规则带上守卫报错以 temperature=0.0 重试 1 次。
"""
from __future__ import annotations

from ..pipeline.guard import ScriptGuardError, validate_script_ast
from .base import LLMCallError, RoleRunner, strip_code_fence

CODER_PROMPT_VERSION = "coder@1.0.0"

CODER_SCHEMA = {
    "required": ["module", "filename", "purpose", "declared_outputs", "code"],
}


class ScriptGenerationError(Exception):
    """生成失败或生成结果未通过静态守卫（含一次降温重试）。"""


def extract_code(runner_output: dict) -> str:
    """从 coder 输出取源码，剥离可能残留的 Markdown 代码围栏。"""
    return strip_code_fence(str(runner_output.get("code", "")))


def make_runner(**kwargs) -> RoleRunner:
    """构造 coder 角色的 RoleRunner（可注入 config/db_path 便于测试）。"""
    return RoleRunner("coder", CODER_PROMPT_VERSION, CODER_SCHEMA, **kwargs)


def generate_script(task_spec: dict, prev_error: str | None = None,
                    runner: RoleRunner | None = None) -> dict:
    """按任务规格生成脚本，返回含 code/figure_specs 的 dict。

    流程：RoleRunner 生成 → 取 code → guard 校验；违规则把违规信息并入输入、
    以 temperature=0.0 再试 1 次；仍不过则抛 ScriptGenerationError（不返回违规脚本）。
    """
    runner = runner or make_runner()
    payload = dict(task_spec)
    if prev_error:
        payload["prev_error"] = prev_error

    output = runner.run(payload)
    code = extract_code(output)
    try:
        validate_script_ast(code)
        output["code"] = code
        return output
    except ScriptGuardError as exc:
        guarded = dict(task_spec)
        guarded["prev_error"] = f"上一次生成的脚本未通过静态守卫，请修正：\n{exc}"
        try:
            output2 = runner.run(guarded, temperature=0.0)
        except LLMCallError as exc2:
            raise ScriptGenerationError(f"守卫未通过且重试调用失败：{exc2}") from exc2
        code2 = extract_code(output2)
        try:
            validate_script_ast(code2)
        except ScriptGuardError as exc2:
            raise ScriptGenerationError(f"两次生成均未通过静态守卫：{exc2}") from exc2
        output2["code"] = code2
        return output2
