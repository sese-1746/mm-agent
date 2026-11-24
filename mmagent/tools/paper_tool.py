"""FR-7 论文装配：**确定性**骨架装配（不调用任何 LLM）。

契约来源：`.agent-runs/2026-10-18/task-008/README.md` §R2 / §R3。

为什么是确定性的（§0-R1）：规划书 §8.4 要求「**不得撰写摘要与论证段落**」——
既然论证不许写，正文就没有需要模型生成的内容；规划书总则也规定「凡有唯一正确答案、
可枚举、可回归的判断一律由 Python 脚本完成」。故本模块只用 `python-docx` 排结构。

三条不可让渡的口径：
1. **每个非空段落都在 manifest 里有 `origin`**（`agent`/`human`/`placeholder`），
   正文段落用 `mm-origin-*` **段落样式**承载（Word 里可见可查），标题**保留 `Heading N`**
   （否则丢大纲级别：导航窗格/自动目录/PDF 书签全失效）、图表题优先套 `Caption`；
   段落样式名与 origin 都写进 sidecar `*.manifest.json` 逐段一条。
2. **不编造**：没有符号数据只出表头 + 占位行；没有图就没有图题；没有 `results/solve.py`
   就只有一段「未找到」的占位说明。文献一条都不生成。
3. **幂等**：同一输入两次生成的正文段落文本列表与 manifest 的 `text_sha256` 序列逐字相同
   （docx 二进制含时间戳，故不比字节）。

本轮**不嵌图**（只出图题占位与编号）——嵌图属 FR-12 导出后的呈现细节，见 §T1 第 3 点；
不写页脚、不写署名（FR-12 的事）。

第 24 轮（《业务场景需求说明书》场景一）：骨架补到 **11 个标准章节**，并在 manifest 里
给出 `template_coverage`（模板化章节覆盖率，口径「纯标题不计」）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.shared import Pt

from ..store.db import DEFAULT_DB_PATH, utcnow
from . import profile_tool

# ---------------------------------------------------------------- 常量（§R3）
ORIGIN_AGENT = "mm-origin-agent"
ORIGIN_HUMAN = "mm-origin-human"
ORIGIN_PLACEHOLDER = "mm-origin-placeholder"
ORIGINS: tuple[str, ...] = (ORIGIN_AGENT, ORIGIN_HUMAN, ORIGIN_PLACEHOLDER)

#: 图题只认真实存在的这两类位图（§T1-3 原文 `*.png|*.jpg`）
FIGURE_SUFFIXES: tuple[str, ...] = (".png", ".jpg")
#: sidecar 后缀：`paper.docx` → `paper.manifest.json`（§R3）
MANIFEST_SUFFIX = ".manifest.json"

DEFAULT_FIGURES_DIR = "figures"
DEFAULT_RESULTS_DIR = "results"
SOLVE_SCRIPT_NAME = "solve.py"
#: 图题/表题样式名（存在就套，Word 里可生成图表目录）
CAPTION_STYLE = "Caption"

SYMBOL_HEADERS: tuple[str, ...] = ("符号", "含义", "单位", "来源")
SYMBOL_PLACEHOLDER_ROW: tuple[str, ...] = ("（待人工填写）", "", "", "")

# ------------------------------------------------- 占位文本白名单（第 21 轮补丁）
#: 占位正文允许的**开头**句式（一条一个登记点，新增占位句必须同步登记）
PH_PREFIXES: tuple[str, ...] = (
    "（摘要待人工撰写：",
    "（题面摘要待人工撰写。",
    "（档案中无子问题：请先完成 FR-1 题面解析",
    "（档案中无子问题，无法生成 5.x 小节",
    "（问题分析待人工撰写：",
    "（模型假设待人工撰写：",
    "（档案中无符号数据：",
    "（以下图题按 figures/ 下文件名排序生成",
    "（模型检验与灵敏度分析待人工撰写：",
    "（可插入的数据位，仅列文件名与行列数，内容未读取；检验方法与结论待人工撰写：",
    "（模型评价与推广待人工撰写：",
    "（参考文献待人工补充：",
    "（附录 B 图表与数据清单：",
    "（附录清单：",
    "（未找到 ",
)
#: 句首无法固定（`5.{i}` 动态编号 / 附录路径）的句式：用「包含」判定
PH_CONTAINS: tuple[str, ...] = (
    "的模型建立与求解正文待人工撰写：",
    "原文逐行插入（等宽字体），来源为 FR-5 求解阶段产物，未做改写。）",
)
#: 占位句必须带的「自证词」：说明这里为什么是空的、该找谁补
PH_MARKERS: tuple[str, ...] = (
    "待人工撰写", "人工填写", "待人工补充", "未找到", "无法生成",
    "请先完成", "未嵌入", "未做改写", "未读取",
)
#: 占位句里**禁止**出现的符号：公式/金额一律视为「编造内容」
PH_FORBIDDEN: tuple[str, ...] = (
    "=", "＝", "＋", "+", "×", "÷", "≤", "≥", "%", "％", "元", "人民币",
)

#: 带**动态路径槽**的占位句式：**逐字模板**，`{path}` 由调用方传入的真实
#: `…/solve.py` 路径替换成 `<path>` 后与模板**全等比对**。
#: 为什么不用正则槽：第 21 轮复核方给出反例——槽写 `[^（）\n]{0,200}` 时，下面这句
#: 「（未找到 本文建立混合integer规划模型，解得 y=12x+3，最优利润 999 元。solve.py：…）」
#: 能整体通过白名单并落进 docx。改成「先替换真实路径、再与常量模板全等」后，槽里
#: 塞任何东西都不等于模板，直接拒——不是收紧近似，而是取消自由度。
PATH_SLOT_TOKEN = "<path>"
PH_PAPER_TEMPLATES: tuple[str, ...] = (
    "（未找到 {path}：代码位留空。请先完成 FR-5 求解，再重新装配。）",
    "（以下为 {path} 原文逐行插入（等宽字体），来源为 FR-5 求解阶段产物，未做改写。）",
)
#: 宽松模式（调用方不提供真实路径时）的兜底正则：只允许 `<slot>` 里出现
#: **路径字符**（字母数字中文/下划线/连字符/点/斜杠/百分号/等号/括号外的常见符号），
#: 且不许出现句读——伪造句必然带「，。；」。
PH_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^（未找到 [^（）\n，。；！？、：]{0,200}solve\.py：代码位留空。"
               r"请先完成 FR-5 求解，再重新装配。）$"),
    re.compile(r"^（以下为 [^（）\n，。；！？、：]{0,200}solve\.py 原文逐行插入（等宽字体），"
               r"来源为 FR-5 求解阶段产物，未做改写。）$"),
)


def placeholder_text_problem(text: str, path_slots: tuple[str, ...] | None = None
                             ) -> str | None:
    """占位正文白名单判定：返回 `None` 表示合规，否则返回拒因。

    为什么需要这道闸：第 21 轮验收把摘要占位句替换成「已撰写摘要 + 公式
    y＝12x＋3 + 编造 999 元」后，原有两条承重墙（无 `agent` 样式、无 OMML 对象）
    **照样绿**——它们只证明"格式没变"，证明不了"内容没编"。所以这里把"哪几种句子
    才允许出现在占位位"写成**封闭清单**：越界即在构建期报错，而不是等人工肉眼发现。

    参数
    ----
    path_slots : 本次构建里真实存在的 `…/solve.py` 路径串。给了就走**严格模式**：
                 把这些路径替换成 `PATH_SLOT_TOKEN` 后与 `PH_PAPER_TEMPLATES`
                 **逐字全等**（槽内塞任何东西都不等于模板 → 拒）。不给则退回宽松
                 正则（只给独立调用方/单测用，槽内禁止句读）。
    """
    body = (text or "").strip()
    if not body:
        return "占位正文不得为空"
    if not (body.startswith("（") and body.endswith("）")):
        return "占位正文须用全角括号包裹（提示此处待人工）"
    # 先处理「带真实路径」的句式：路径里可能有 `%`/`=`/`元`（F6：`res_100%`、
    # `a=b`、`单元测试` 都曾把旧版判死），故这两句豁免 PH_FORBIDDEN，改用
    # 「路径替换 + 模板全等」或「正则槽禁句读」判定形状。
    if path_slots is not None:
        normalized = body
        for slot in path_slots:
            if slot:
                normalized = normalized.replace(str(slot), PATH_SLOT_TOKEN)
        if normalized != body:                       # 真的替换到了路径
            if normalized in {tpl.format(path=PATH_SLOT_TOKEN)
                              for tpl in PH_PAPER_TEMPLATES}:
                return None
            return "含路径的占位句与登记模板不逐字相等（疑似在路径槽里塞了内容）"
        # 严格模式下，凡以「（未找到 /（以下为」开头的都是**路径句式**，里面必须出现
        # 本次构建真实的 `…/solve.py` 串；换个文件名（`other.py`）也属伪造。
        if body.startswith(("（未找到 ", "（以下为 ")):
            return "路径占位句里没有本次构建的真实 solve.py 路径（疑似伪造或过期）"
    if any(pat.match(body) for pat in PH_PATH_PATTERNS):
        return None
    hit = [c for c in PH_FORBIDDEN if c in body]
    if hit:
        return f"占位正文出现公式/金额符号 {hit!r}（视为编造内容）"
    if not any(mark in body for mark in PH_MARKERS):
        return ("占位正文缺少自证词（应含：待人工撰写/人工填写/待人工补充/未找到/"
                "无法生成/请先完成/未嵌入/未做改写）")
    if any(body.startswith(p) for p in PH_PREFIXES):
        return None
    if any(c in body for c in PH_CONTAINS):
        return None
    return "占位正文不在白名单句式内（疑似已撰写正文内容）"

CHAPTER_ABSTRACT = "摘要"
CHAPTER_RESTATE = "一、问题重述"
CHAPTER_ANALYSIS = "二、问题分析"
CHAPTER_ASSUMPTION = "三、模型假设"
CHAPTER_SYMBOLS = "四、符号说明"
CHAPTER_MODEL = "五、模型建立与求解"
CHAPTER_VALIDATION = "六、模型检验与灵敏度分析"
CHAPTER_EVAL = "七、模型评价与推广"
CHAPTER_REFS = "参考文献"
CHAPTER_APPENDIX = "附录 A 求解代码"
CHAPTER_APPENDIX_B = "附录 B 图表与数据清单"

#: §R2 固定骨架（写死便于人工改；顺序即 docx 中的章序）
#: 第 24 轮：按《智能体业务场景需求说明书》场景一补到 **11 个标准章节**
#: （原「三、模型假设与符号说明」拆成三/四两章，后续章序整体后移一位）。
PAPER_SKELETON: tuple[tuple[str, str], ...] = (
    (CHAPTER_ABSTRACT, "仅占位，不得撰写"),
    (CHAPTER_RESTATE, "题面摘要占位 + 子问题清单（档案字段原样转述）"),
    (CHAPTER_ANALYSIS, "占位，提示人工填写"),
    (CHAPTER_ASSUMPTION, "占位，提示人工填写"),
    (CHAPTER_SYMBOLS, "表 1 符号说明（档案有符号就填表，没有只出表头 + 占位行）"),
    (CHAPTER_MODEL, "每子问题一个 5.x 小节 + 图表位"),
    (CHAPTER_VALIDATION, "占位；results/ 下有 CSV 时只列文件名与行列数作为可插入数据位"),
    (CHAPTER_EVAL, "占位"),
    (CHAPTER_REFS, "占位，不得编造文献"),
    (CHAPTER_APPENDIX, "代码位：results/solve.py 原文等宽插入"),
    (CHAPTER_APPENDIX_B, "图表与数据清单：图 N 文件名 + results/*.csv 的行列数"),
)


# ---------------------------------------------------------------- 样式
def _install_origin_styles(doc) -> None:
    """创建三个 `mm-origin-*` 段落样式（幂等：已存在就复用）。"""
    for name in ORIGINS:
        try:
            doc.styles[name]
        except KeyError:
            style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
            style.font.name = "Times New Roman"
            style.font.size = Pt(12)
            # 中文正文字体单独指定，避免 Word 回落到默认宋体以外的字体
            rpr = style.element.get_or_add_rPr()
            fonts = rpr.find(qn("w:rFonts"))
            if fonts is None:
                fonts = rpr.makeelement(qn("w:rFonts"), {})
                rpr.append(fonts)
            fonts.set(qn("w:eastAsia"), "宋体")


def _mono(run) -> None:
    """把 run 设成等宽字体（附录代码位）。"""
    run.font.name = "Consolas"
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(fonts)
    fonts.set(qn("w:eastAsia"), "Consolas")


# ---------------------------------------------------------------- 段落写入器
class _Builder:
    """逐段写入并记账：manifest 与 docx 段落**同一处**产生，天然双向对齐。"""

    def __init__(self, doc, path_slots: tuple[str, ...] | None = None) -> None:
        self.doc = doc
        self.entries: list[dict] = []
        self.chapter = ""
        #: 真实存在的 `…/solve.py` 路径串（供占位句**严格模式**做模板全等比对）
        self.path_slots: tuple[str, ...] | None = path_slots
        #: 章名 → 该章已写入的「内容位」种类（`paragraph`/`caption`/`table`/`code`）。
        #: 口径见 `_template_coverage`：**纯标题不计**（只有标题的章不产生任何键）。
        self.chapter_content: dict[str, set[str]] = {}

    def mark_content(self, kind: str) -> None:
        """登记当前章的一个「内容位」（表格/代码这类不走 `body()` 的入口自己调）。"""
        if self.chapter:
            self.chapter_content.setdefault(self.chapter, set()).add(kind)

    def heading(self, text: str, level: int, chapter: str,
                origin: str = ORIGIN_PLACEHOLDER):
        """标题段落：**保留 `Heading N` 样式**，只把 origin 记进 manifest。

        为什么不再覆盖成 `mm-origin-*`：覆盖后段落丢失大纲级别 → Word 导航窗格看不到
        章节、自动目录与 PDF 书签都生成不了（第 21 轮补丁）。origin 仍可查——manifest
        里每段一条，标题的 origin 恒为 `placeholder`（本工具不撰写标题外的任何内容）。
        """
        para = self.doc.add_heading(text, level=level)
        self.chapter = chapter
        self._record(para, origin)
        return para

    def caption(self, text: str, origin: str = ORIGIN_PLACEHOLDER):
        """图题/表题：有 `Caption` 样式就套它（Word 里可做图表目录），否则退回 origin 样式。"""
        para = self.doc.add_paragraph()
        try:
            para.style = self.doc.styles[CAPTION_STYLE]
        except KeyError:                     # 模板无 Caption 样式时保持旧行为
            para.style = self.doc.styles[origin]
        para.add_run(text)
        self._record(para, origin)
        self.mark_content("caption")
        return para

    def body(self, text: str, origin: str = ORIGIN_PLACEHOLDER, *,
             mono: bool = False, from_archive: str | bool = False):
        # 白名单闸：占位正文只许落在登记句式里（见 `placeholder_text_problem`）。
        # 放在写入前而不是测试里，才能在构建期就炸，而不是靠人事后肉眼抽检。
        # `from_archive=True` 是**转述**（档案字段原样搬过来，可能天然带「＝」「元」
        # 「%」），不属于"本工具撰写的占位句"，故豁免白名单；其真实性由「转述行必须
        # 逐字等于档案字段拼装结果」的用例单独把关。
        if origin == ORIGIN_PLACEHOLDER and not from_archive:
            problem = placeholder_text_problem(text, path_slots=self.path_slots)
            if problem:
                raise ValueError(f"占位正文越界：{problem}（文本 {text[:60]!r}）")
        para = self.doc.add_paragraph()
        para.style = self.doc.styles[origin]
        run = para.add_run(text)
        if mono:
            _mono(run)
        self._record(para, origin, from_archive=from_archive)
        if text.strip():                 # 代码空行不算「内容位」（否则空 solve.py 也计入）
            self.mark_content("code" if mono else "paragraph")
        return para

    def _record(self, para, origin: str, *, from_archive: str | bool = False) -> None:
        text = para.text
        if not text.strip():          # 空段（如代码空行）不进 manifest，但仍带样式
            return
        entry = {
            "chapter": self.chapter,
            "paragraph_index": len(self.doc.paragraphs) - 1,
            "style_name": para.style.name,
            "origin": origin,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text_preview": text[:60],
        }
        if from_archive:
            # 转述标记：内容源头是档案字段，非本工具撰写。值尽量给**来源字段名**
            # （验收方 C2 指出：只记布尔无法判断"这条到底转述自哪儿"）。
            entry["from_archive"] = from_archive if isinstance(from_archive, str) else True
        self.entries.append(entry)


# ---------------------------------------------------------------- 输入归一化
def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _subproblems(patch: dict) -> list[dict]:
    raw = patch.get("subproblems")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _scan_figures(figures_dir: str | Path | None) -> list[Path]:
    """扫 `figures_dir` 下真实存在的 `*.png|*.jpg`，按文件名排序（不编造图）。"""
    root = Path(figures_dir) if figures_dir is not None else Path(DEFAULT_FIGURES_DIR)
    if not root.is_dir():
        return []
    files = [p for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in FIGURE_SUFFIXES]
    return sorted(files, key=lambda p: p.name)


def _scan_result_tables(results_dir: str | Path | None) -> list[dict]:
    """扫 `results/` 下的 `*.csv`，**只数行列**（不读内容、不据此下任何结论）。

    口径：`rows` = 表头之外的**非空数据行**数，`cols` = 表头列数。只用于「可插入的
    数据位」清单（第六、七章与附录 B），任何统计结论都留给人工撰写。
    """
    root = Path(results_dir) if results_dir is not None else Path(DEFAULT_RESULTS_DIR)
    if not root.is_dir():
        return []
    tables: list[dict] = []
    for path in sorted((p for p in root.iterdir()
                        if p.is_file() and p.suffix.lower() == ".csv"),
                       key=lambda p: p.name):
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            cols = len(header)
            rows = sum(1 for row in reader if any(str(cell).strip() for cell in row))
        tables.append({"file": path.name, "rows": rows, "cols": cols})
    return tables


def _template_coverage(slots: dict[str, set[str]]) -> dict:
    """模板化章节覆盖率（文档口径：**「纯标题不计」**）。

    判据：某一章除标题之外还有「正文段 / 表格 / 图题位 / 代码位」**之一**，才计入
    `with_content_slot`；只有标题（`slots` 里没有该章的键）的章不计入。
    `ratio = with_content_slot / chapters`，`chapters` 恒为 `PAPER_SKELETON` 的章数。
    """
    names = [name for name, _ in PAPER_SKELETON]
    total = len(names)
    with_slot = sum(1 for name in names if slots.get(name))
    return {"chapters": total, "with_content_slot": with_slot,
            "ratio": round(with_slot / total, 4) if total else 0.0}


def _symbol_rows(patch: dict) -> list[tuple[str, str, str, str]]:
    """从档案取符号表；档案无符号数据 → 返回空列表（调用方补占位行）。"""
    raw = patch.get("symbols")
    if not isinstance(raw, list):
        return []
    rows: list[tuple[str, str, str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append(tuple(
            _as_str(item.get(cn)) or _as_str(item.get(en))
            for cn, en in zip(SYMBOL_HEADERS, ("symbol", "meaning", "unit", "source"))))
    return rows


def _sub_problem_line(index: int, sub: dict) -> str:
    """子问题清单行：**只转述档案字段**，不补写、不改写。"""
    label = _as_str(sub.get("label")) or f"子问题{index}"
    target = _as_str(sub.get("target")) or "（档案缺失）"
    given = _as_str(sub.get("given")) or "（档案缺失）"
    constraints = _as_str(sub.get("constraints")) or "（档案缺失）"
    return f"{label}：要什么＝{target}；给什么＝{given}；约束＝{constraints}"


def _heading_levels(doc) -> dict[str, int]:
    """统计各级 `Heading N` 段落数，键形如 `h1`/`h2`（供 `has_outline` 机检）。"""
    levels: dict[str, int] = {}
    for para in doc.paragraphs:
        name = para.style.name or ""
        if not name.startswith("Heading "):
            continue
        suffix = name.split(" ", 1)[1].strip()
        if not suffix.isdigit():
            continue
        key = f"h{int(suffix)}"
        levels[key] = levels.get(key, 0) + 1
    return dict(sorted(levels.items()))


# ---------------------------------------------------------------- 主入口
def build_paper(*, profile_id: str, version: str | int = "last",
                figures_dir: str | Path | None = None,
                results_dir: str | Path | None = None,
                out_path: str | Path, actor: str = "assembler",
                db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """按 §R2 固定骨架把赛题档案装配成 docx + sidecar manifest。

    第 24 轮：骨架为 **11 个标准章节**（见 `PAPER_SKELETON`），manifest 额外给出
    `template_coverage`（模板化章节覆盖率），口径为「**纯标题不计**」——某章除标题
    外还有正文段/表格/图题位/代码位之一，才计入 `with_content_slot`。

    参数
    ----
    profile_id / version : 档案与版本（`"last"` 取最新 adopted，无 adopted 取最新版）
    figures_dir          : 图题来源目录（默认 `figures/`）；**没有图就没有图题**
    results_dir          : 附录代码来源目录（默认 `results/`），读 `solve.py`；
                           另外只数 `*.csv` 的行列数（不读内容）供第六/附录 B 用
    out_path             : docx 落盘路径；sidecar 为其同名 `*.manifest.json`
    actor                : 装配者标识（本轮固定 `assembler`，仅记账，不调 LLM）

    异常
    ----
    KeyError : 档案或版本不存在（CLI 转 exit 2）
    OSError  : 写盘失败（CLI 转 exit 3）
    """
    profile = profile_tool.read(profile_id, version, db_path=db_path)
    patch = profile.get("patch") or {}
    subs = _subproblems(patch)
    figures = _scan_figures(figures_dir)
    symbol_rows = _symbol_rows(patch)

    out = Path(out_path)
    doc = Document()
    _install_origin_styles(doc)
    # `solve.py` 的真实路径要先算出来：附录 A 的占位句里会出现它，而白名单严格模式
    # 需要拿这个真实串去替换路径槽（否则路径含 `%`/`=`/`元` 时会被当公式误判，F6）。
    solve_root = Path(results_dir) if results_dir is not None else Path(DEFAULT_RESULTS_DIR)
    solve_path = solve_root / SOLVE_SCRIPT_NAME
    result_tables = _scan_result_tables(solve_root)
    b = _Builder(doc, path_slots=(solve_path.as_posix(),))

    # ---- 0 摘要（全部 placeholder：本工具不撰写摘要）
    b.heading(CHAPTER_ABSTRACT, 1, CHAPTER_ABSTRACT)
    b.body("（摘要待人工撰写：本工具只做结构装配，不撰写摘要，也不撰写任何论证段落。"
           "请在第五、六章定稿后据结论人工补写。）")

    # ---- 1 一、问题重述
    b.heading(CHAPTER_RESTATE, 1, CHAPTER_RESTATE)
    b.body(f"（题面摘要待人工撰写。以下子问题清单仅为档案 v{profile['version_id']} "
           f"字段的原样转述，未做任何改写或补写。）")
    if subs:
        for i, sub in enumerate(subs, 1):
            b.body(_sub_problem_line(i, sub), from_archive="subproblems")
    else:
        b.body("（档案中无子问题：请先完成 FR-1 题面解析，再重新装配。）")

    # ---- 2 二、问题分析
    b.heading(CHAPTER_ANALYSIS, 1, CHAPTER_ANALYSIS)
    b.body("（问题分析待人工撰写：本工具不生成分析性/论证性文字。）")

    # ---- 3 三、模型假设（原 3.1 二级标题升为章标题，不再保留 3.x 子节）
    b.heading(CHAPTER_ASSUMPTION, 1, CHAPTER_ASSUMPTION)
    b.body("（模型假设待人工撰写：本工具不从题面推断假设，也不生成假设条文。）")

    # ---- 4 四、符号说明（原 3.2 二级标题升为章标题）
    b.heading(CHAPTER_SYMBOLS, 1, CHAPTER_SYMBOLS)
    b.caption("表 1 符号说明")
    table = doc.add_table(rows=1, cols=len(SYMBOL_HEADERS))
    table.style = "Table Grid"
    for cell, header in zip(table.rows[0].cells, SYMBOL_HEADERS):
        cell.text = header
        for run in cell.paragraphs[0].runs:
            run.bold = True
    data_rows = symbol_rows or [
        tuple(_as_str(v) for v in SYMBOL_PLACEHOLDER_ROW)]
    for row in data_rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = value
    b.mark_content("table")              # 表格不走 body()，内容位手工登记
    if not symbol_rows:
        b.body("（档案中无符号数据：上表仅保留表头与一行占位，符号须由人工填写，"
               "本工具不编造符号。）")
    # 表格单元格不走 `doc.paragraphs`，天然逃出「段落双向对齐」的覆盖——第 21 轮
    # 验收指出这是盲区（往单元格里塞编造内容不会被任何用例发现）。故把**每一格**
    # 的文本原样记进 manifest，供「docx 单元格 ↔ manifest.cells」双向比对。
    table_cells: list[list[str]] = [list(SYMBOL_HEADERS)]
    table_cells += [list(row) for row in data_rows]
    table_meta = {"caption": "表 1 符号说明", "headers": list(SYMBOL_HEADERS),
                  "rows": len(data_rows), "data_rows": len(symbol_rows),
                  "cells": table_cells,
                  "cell_count": sum(len(r) for r in table_cells)}

    # ---- 5 五、模型建立与求解
    b.heading(CHAPTER_MODEL, 1, CHAPTER_MODEL)
    if subs:
        for i, sub in enumerate(subs, 1):
            label = _as_str(sub.get("label")) or f"子问题{i}"
            b.heading(f"5.{i} {label}", 2, CHAPTER_MODEL)
            b.body(f"（5.{i} 的模型建立与求解正文待人工撰写：本工具只保留结构与图表位，"
                   f"不生成论证。）")
    else:
        b.body("（档案中无子问题，无法生成 5.x 小节；请先完成 FR-1 题面解析。）")

    figure_meta: list[dict] = []
    if figures:
        slot = len(subs) + 1
        b.heading(f"5.{slot} 图表", 2, CHAPTER_MODEL)
        b.body("（以下图题按 figures/ 下文件名排序生成；本轮**未嵌入**图片，"
               "嵌图属 FR-12 导出后的呈现细节。）")
        for no, path in enumerate(figures, 1):
            caption = f"图 {no} {path.name}"
            b.caption(caption)
            figure_meta.append({"no": no, "file": path.name, "caption": caption})

    # ---- 6 六、模型检验与灵敏度分析
    b.heading(CHAPTER_VALIDATION, 1, CHAPTER_VALIDATION)
    b.body("（模型检验与灵敏度分析待人工撰写：本工具不生成检验方法、判定指标，"
           "也不得出灵敏度结论。）")
    if result_tables:
        # 只列「文件名 + 行列数」当可插入的数据位：读内容就会滑向"编造结论"，
        # 故行列数由 `_scan_result_tables` 数出来，结论一律留给人工。
        slots_text = "；".join(
            f"{item['file']}（{item['rows']} 行 {item['cols']} 列）"
            for item in result_tables)
        b.body("（可插入的数据位，仅列文件名与行列数，内容未读取；检验方法与结论待人工"
               f"撰写：{slots_text}。）")

    # ---- 7 七、模型评价与推广
    b.heading(CHAPTER_EVAL, 1, CHAPTER_EVAL)
    b.body("（模型评价与推广待人工撰写：本工具不生成评价性文字。）")

    # ---- 8 参考文献（一条都不生成）
    b.heading(CHAPTER_REFS, 1, CHAPTER_REFS)
    b.body("（参考文献待人工补充：本工具不检索、不生成、不编造任何文献条目。）")

    # ---- 9 附录 A 求解代码
    b.heading(CHAPTER_APPENDIX, 1, CHAPTER_APPENDIX)
    appendix_code = solve_path.is_file()
    if appendix_code:
        b.body(f"（以下为 {solve_path.as_posix()} 原文逐行插入（等宽字体），"
               f"来源为 FR-5 求解阶段产物，未做改写。）")
        for line in solve_path.read_text(encoding="utf-8-sig").splitlines():
            b.body(line, ORIGIN_AGENT, mono=True)
    else:
        b.body(f"（未找到 {solve_path.as_posix()}：代码位留空。请先完成 FR-5 求解，"
               f"再重新装配。）")

    # ---- 10 附录 B 图表与数据清单（只列已存在的东西；都没有就一句占位）
    b.heading(CHAPTER_APPENDIX_B, 1, CHAPTER_APPENDIX_B)
    if not figures and not result_tables:
        b.body("（附录 B 图表与数据清单：figures/ 与 results/ 下均无可列出项，"
               "图表待人工补充。）")
    else:
        for no, path in enumerate(figures, 1):
            # 清单行用占位句包裹（不套 Caption 样式）：避免与第五章的图题编号重复计数，
            # 同时保证这句仍走白名单闸（前缀 `（附录清单：` + 自证词「未嵌入」）。
            b.body(f"（附录清单：图 {no} {path.name} 已在第五章出图题，"
                   f"本轮未嵌入图片本体。）")
        for item in result_tables:
            b.body(f"（附录清单：{item['file']}，{item['rows']} 行 "
                   f"{item['cols']} 列；仅列文件名与行列数，内容未读取。）")

    # ---- 落盘
    manifest_path = out.with_suffix(MANIFEST_SUFFIX)
    counts = {"paragraphs": len(b.entries),
              "figures": len(figure_meta),
              "placeholders": sum(1 for e in b.entries
                                  if e["origin"] == ORIGIN_PLACEHOLDER)}
    levels = _heading_levels(doc)
    # 模板化章节覆盖率：口径「纯标题不计」——见 `_template_coverage` 的 docstring。
    # 这里统计的是**结构位是否存在**（正文段/表格/图题位/代码位之一），
    # 不是"内容是否已撰写"；内容一律由人工补写。
    coverage = _template_coverage(b.chapter_content)
    manifest = {
        "tool": "paper_tool.build_paper",
        "skeleton": [{"chapter": name, "content": note}
                     for name, note in PAPER_SKELETON],
        "source": {"profile_id": profile["profile_id"],
                   "version_id": profile["version_id"],
                   "status": profile.get("status", "")},
        "actor": actor,
        "generated_at": utcnow(),
        "out_path": str(out),
        "appendix_code": appendix_code,
        "counts": counts,
        # 「大纲可用」不再只靠样式名断言：标题保留 Heading N，这里给出各级计数
        "heading_levels": levels,
        "has_outline": levels.get("h1", 0) > 0,
        # `template_coverage`：11 章里有几章「除标题外还有内容位」（纯标题不计）
        "template_coverage": coverage,
        "chapter_content": {k: sorted(v) for k, v in sorted(b.chapter_content.items())},
        "figures": figure_meta,
        "tables": [table_meta],
        "result_tables": result_tables,
        "paragraphs": b.entries,
    }
    doc.save(str(out))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "out_path": str(out),
        "manifest_path": str(manifest_path),
        "paragraphs": counts["paragraphs"],
        "figures": counts["figures"],
        "placeholders": counts["placeholders"],
        "appendix_code": appendix_code,
        "profile_version_id": int(profile["version_id"]),
    }
