"""FR-2 体检的 LLM 增强：用自然语言解释口径冲突（骨架）。

确定性冲突检测见 intake._detect_conflicts；本模块只负责"翻译成人话"，
不产出新结论，因此 LLM 失败时直接降级为模板化摘要，不阻断主流程。
"""
from __future__ import annotations


def explain_conflicts(conflicts: list[dict], budget_tokens: int = 2000) -> str:
    """把冲突清单解释成给建模队员看的说明文字。

    TODO(Phase-next)：调 agents.base.RoleRunner('archivist') 生成解释；
    本轮骨架：无 LLM 环境下返回模板化摘要。
    """
    if not conflicts:
        return "未检出口径冲突，可进入字段映射确认环节。"
    lines = [f"共检出 {len(conflicts)} 条口径冲突，建议逐条人工确认："]
    for i, c in enumerate(conflicts, 1):
        lines.append(f"{i}. [{c['type']}] {c['message']}")
    lines.append("（LLM 解释器未接入，本摘要由模板生成。）")
    return "\n".join(lines)
