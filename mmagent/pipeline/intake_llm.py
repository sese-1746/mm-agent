"""FR-2 体检的 LLM 增强：用自然语言解释口径冲突。

确定性冲突检测见 intake._detect_conflicts；本模块只负责"翻译成人话"，
不产出新结论，因此 LLM 失败（无 runner / 超时 / 输出不可用）时直接降级为模板化摘要，
**不阻断主流程**——这条承诺优先于「必须用上 LLM」。
"""
from __future__ import annotations


def _template(conflicts: list[dict]) -> str:
    lines = [f"共检出 {len(conflicts)} 条口径冲突，建议逐条人工确认："]
    for i, c in enumerate(conflicts, 1):
        lines.append(f"{i}. [{c['type']}] {c['message']}")
    lines.append("（LLM 解释器未接入，本摘要由模板生成。）")
    return "\n".join(lines)


def explain_conflicts(conflicts: list[dict], budget_tokens: int = 2000, *,
                      runner=None) -> str:
    """把冲突清单解释成给建模队员看的说明文字。

    `runner` 注入时调 archivist 生成解释（输出取 `explanation` 或 `summary`），
    任意异常/空结果一律降级为模板（docstring 承诺「不阻断主流程」）。
    """
    if not conflicts:
        return "未检出口径冲突，可进入字段映射确认环节。"
    if runner is not None:
        try:
            out = runner.run({"conflicts": conflicts, "budget_tokens": budget_tokens})
            if isinstance(out, dict):
                text = str(out.get("explanation") or out.get("summary") or "").strip()
                if text:
                    return text
        except Exception:  # noqa: BLE001  解释器失败不得阻断体检主流程
            pass
    return _template(conflicts)
