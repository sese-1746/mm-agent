"""FR-6 论文出图风格：中文字体 fallback、dpi300、负号正常显示。"""
from __future__ import annotations

# 中文字体优先级：宋体（论文正文）→ 黑体 → 微软雅黑 → 系统兜底
CJK_FONT_CANDIDATES = ["SimSun", "SimHei", "Microsoft YaHei", "Noto Sans CJK SC",
                       "DejaVu Sans"]


def apply_paper_style() -> None:
    """设置 matplotlib rcParams 为论文出图风格（幂等）。"""
    import matplotlib

    matplotlib.use("Agg", force=True)  # 无显示环境强制后端
    rc = matplotlib.rcParams
    rc["font.sans-serif"] = CJK_FONT_CANDIDATES
    rc["axes.unicode_minus"] = False   # 负号用 ASCII 连字符，避免缺字形方块
    rc["figure.dpi"] = 300
    rc["savefig.dpi"] = 300
    rc["savefig.bbox"] = "tight"


def available_cjk_font() -> str | None:
    """返回系统上第一个可用的中文字体名；都没有则返回 None（调用方应告警）。"""
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in CJK_FONT_CANDIDATES:
        if name in installed:
            return name
    return None
