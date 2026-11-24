"""archivist 角色：档案官（题面解析 FR-1、体检解释、口径冲突说明、档案整理）。

FR-1 的核心不是提示词，而是**确定性的反造假后校验**：
`parse_problem` 调完 LLM 后必须用 `_verify_against_source` 拿输出逐条回原文核对——
- `source_quote` 必须是 **str**、非空白字符 ≥ `MIN_QUOTE_CHARS`、且原文子串（归一化后），
  任一不满足 → 该子问题降级进 `open_questions`；
- 子问题**所有字符串字段**（含 `target`/`label`）里的数字必须在原文出现过，否则剔除该分句
  并记 `open_questions`。
这样「不得补写题目没有的数据」是代码保证的，而不是提示词祈祷。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..store.db import DEFAULT_DB_PATH
from .base import RoleRunner

ARCHIVIST_PROMPT_VERSION = "archivist@0.2.0"

# 输出 JSON 的最小约束（顶层三键；FR-1 起不再只要求 summary）
ARCHIVIST_SCHEMA = {"required": ["subproblems", "open_questions", "summary"]}

# 输入上限（规划书 §8.1：输入 ≤6k 字符）；超限必须可见地截断，不许静默
PROBLEM_TEXT_LIMIT = 6000

# 三要素字段名。模型可能用同义键名（want/goal/ask/question），故做一层窄别名映射。
_TARGET_KEYS = ("target", "want", "goal", "ask", "question")
_GIVEN_KEYS = ("given", "inputs", "data")
_CONSTRAINT_KEYS = ("constraints", "constraint", "limits")

_WS_RE = re.compile(r"\s+")
# 数字 token（含小数与百分号）；NFKC 后全角数字/％ 已归一为半角
_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
# 分句分隔符（保留在切分结果中，便于原样重组）
_CLAUSE_SEP_RE = re.compile(r"([，,；;。！？!?\n])")
_CLAUSE_TAIL = "，,；;。、 \t"

# `source_quote` 的最短有效长度（**非空白字符数**）。1 字符引用（`,`、`种`）
# 几乎必然能在任何题面命中，等于把闸①废掉，故设下限（D-41 局限①的收口）。
MIN_QUOTE_CHARS = 4

# `source_quote` 的**低置信阈值**（非空白字符数）。低于它仍可用（合法短引用不该
# 错杀：原文里就写「甲」「乙」时 4 字也可能已是完整一句），但「逐字命中」的偶然
# 性会升高，故打 `quote_confidence="low"` 并记账，供人工复核。
# 口径说清楚（验收方 C3 指出「只标不拒」四字不够）：**对档案不拒**（子问题照样保留、
# 不 drop）；**对导出默认阻断**——因为低置信会追加一条 open_question，而 FR-12 的
# 闸 d 是「open_questions 非空即拒」，故此类档案必须显式 `--allow-open-questions`。
SHORT_QUOTE_CHARS = 8


def make_runner(**kwargs) -> RoleRunner:
    return RoleRunner("archivist", ARCHIVIST_PROMPT_VERSION,
                      ARCHIVIST_SCHEMA, **kwargs)


# ---------------------------------------------------------------- 归一化
def _normalize(text: object) -> str:
    """原文/引用的比较口径（**唯一**口径，两处必须一致）。

    依次做四件事：
    1. `unicodedata.normalize("NFKC", ...)`：全角 → 半角（数字、标点、括号、％），
       并把兼容字符折叠成标准字符；
    2. 去首尾空白；
    3. 连续空白（含换行/制表）折叠成**单个空格**——题面 PDF 转文本常在句中硬换行；
    4. 再次去首尾空白。

    **不做**任何模糊匹配（不改字、不同义词、不去标点、不做编辑距离）。
    即：除「字符宽度 + 空白」外，`source_quote` 的每个字符都必须按原顺序连续出现。
    """
    t = unicodedata.normalize("NFKC", str(text or ""))
    return _WS_RE.sub(" ", t).strip()


def _normalize_no_space(text: object) -> str:
    """在 `_normalize` 之上再去掉**全部**空白。

    仅用于兜底「引用跨了原文硬换行、而折叠成空格后仍差一个空格」的情况
    （如原文 `甲\\n乙` 折叠为 `甲 乙`，引用写 `甲乙`）。仍然逐字符连续匹配，
    不会放过任何被改写/新增的字。
    """
    return _WS_RE.sub("", _normalize(text))


def _quote_in_source(quote: str, source: str) -> bool:
    """引用是否逐字命中原文（宽松仅限空白/全半角，见 `_normalize`）。"""
    q = _normalize(quote)
    if not q:
        return False
    if q in _normalize(source):
        return True
    return _normalize_no_space(quote) in _normalize_no_space(source)


def _quote_reject_reason(raw_quote: object, source: str) -> str:
    """`source_quote` 不合格的原因；合格返回空串。

    三道并列要求（任一不满足即整条子问题降级）：
    ① **必须是 str**——`["…"]`、`{"q": …}` 之类一律不命中（不靠 `_as_text` 强转，
       否则模型用数组包装就能绕过「逐字引用」的口径）；
    ② 非空白字符数 ≥ `MIN_QUOTE_CHARS`——挡掉 `,`、`种` 这类 1 字符引用；
    ③ 归一化后必须是原文子串（`_quote_in_source`）。
    """
    if not isinstance(raw_quote, str):
        return (f"source_quote 不是字符串（实际 {type(raw_quote).__name__}），"
                f"无法核对原文，已拒")
    if len(_normalize_no_space(raw_quote)) < MIN_QUOTE_CHARS:
        return (f"source_quote 过短（非空白字符 < {MIN_QUOTE_CHARS}），"
                f"不足以证明来自原文，已拒")
    if not _quote_in_source(raw_quote, source):
        return f"source_quote={raw_quote[:80]!r} 未在题面找到"
    return ""


def _numbers(text: object) -> list[str]:
    """抽取数字 token（保留出现顺序，便于报错可读）。"""
    return _NUM_RE.findall(_normalize(text))


def _as_text(value: object) -> str:
    """把模型给的字符串/数组/None 统一成字符串（数组用「；」连接）。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip()


def _pick(raw: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        text = _as_text(raw.get(key))
        if text:
            return text
    return ""


# ---------------------------------------------------------------- 后校验（核心）
def _strip_bad_numbers(text: str, allowed: set[str], label: str, field: str,
                       open_questions: list[str]) -> str:
    """剔除含「原文没有的数字」的分句，并把每个越界数字记进 `open_questions`。

    分句口径：按 `，,；;。！？!?` 与换行切分（分隔符原样保留）。
    只剔除**命中越界数字的那个分句**，同字段其它分句保留。
    """
    parts = _CLAUSE_SEP_RE.split(text)
    kept: list[str] = []
    for i in range(0, len(parts), 2):
        clause = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        bad = [n for n in _numbers(clause) if n not in allowed]
        if bad:
            for num in bad:
                open_questions.append(
                    f"未在题面出现的数值：{num}（子问题「{label}」的 {field} "
                    f"已剔除该分句，需人工确认）")
        else:
            kept.append(clause + sep)
    return "".join(kept).strip().rstrip(_CLAUSE_TAIL)


def _clean_string_fields(raw: dict, allowed: set[str], label: str,
                         notes: list[str]) -> dict:
    """把该子问题**所有字符串字段**过一遍数字闸（D-41 局限③的收口）。

    `target` / `label` / `given` / `constraints` 及任何别名键（`want`、`goal`、
    `question`…）一律不例外：凡是 str 就查。「只查 given/constraints」等于允许模型把
    编造的数字挪进 `target` 或 `label` 就免疫。
    `source_quote` 跳过（它的数字本身就是 `allowed` 的来源）；非 str 值原样保留。
    """
    cleaned: dict = {}
    for key, value in raw.items():
        if key == "source_quote" or not isinstance(value, str):
            cleaned[key] = value
            continue
        cleaned[key] = _strip_bad_numbers(value, allowed, label, key, notes)
    return cleaned


def _verify_against_source(problem_text: str, subproblems: list) -> tuple[list[dict], list[str], int]:
    """逐条拿 LLM 输出回原文核对，返回 `(保留的子问题, 新 open_questions, dropped)`。

    四道闸：
    ① `source_quote` 必须是 **str**、非空白字符 ≥ `MIN_QUOTE_CHARS`、且归一化后是原文
       子串——否则降级进 open_questions，**不静默丢弃**；
    ② 该子问题**所有字符串字段**（含 `target`/`label` 等）里的数字必须在原文出现过
       ——否则剔除该分句并记账；
    ③ 三要素任一为空——记进 open_questions；
    ④ 引用非空白字符 < `SHORT_QUOTE_CHARS`——**保留**但打 `quote_confidence="low"`
       并记进 open_questions（短引文定位力弱，交人工复核，不错杀）。
    """
    source_numbers = set(_numbers(problem_text))
    kept: list[dict] = []
    notes: list[str] = []
    dropped = 0

    for idx, raw in enumerate(subproblems or [], 1):
        if not isinstance(raw, dict):
            notes.append(f"第 {idx} 条子问题不是 JSON 对象，已降级待人工确认：{raw!r}")
            dropped += 1
            continue

        label_hint = _as_text(raw.get("label")) or f"子问题{idx}"
        raw_quote = raw.get("source_quote")
        quote = raw_quote.strip() if isinstance(raw_quote, str) else ""

        # 闸①：引用必须是 str、有实质长度、且逐字命中原文
        reason = _quote_reject_reason(raw_quote, problem_text)
        if reason:
            dropped += 1
            brief = (_as_text(raw.get("target")) or _as_text(raw.get("summary"))
                     or quote or "（无摘要）")
            notes.append(
                f"该子问题未能对应到原文，已降级待人工确认：{brief[:120]}（{reason}）")
            continue

        # 闸②：数字必须来自原文（只允许 source_quote ∪ 原文 里出现过的 token）
        allowed = source_numbers | set(_numbers(quote))
        cleaned = _clean_string_fields(raw, allowed, label_hint, notes)

        label = _as_text(cleaned.get("label")) or f"子问题{idx}"
        target = _pick(cleaned, _TARGET_KEYS)
        given = _pick(cleaned, _GIVEN_KEYS)
        constraints = _pick(cleaned, _CONSTRAINT_KEYS)

        # 闸③：三要素不许缺
        missing = [name for name, val in
                   (("target（要什么）", target), ("given（给什么）", given),
                    ("constraints（约束）", constraints)) if not val]
        if missing:
            notes.append(f"子问题「{label}」三要素缺失：{'、'.join(missing)}，"
                         f"需人工确认（未做任何补写）")

        entry = {
            "label": label,
            "target": target,
            "given": given,
            "constraints": constraints,
            "source_quote": quote,
        }
        # 闸④（第 21 轮验收补）：短引文打低置信标记。只标不拒——4~7 字的引用可能
        # 就是原文的完整短句，拒掉会误杀；但"刚好逐字命中"的偶然性随长度上升，
        # 由人工复核出处比静默接受更诚实。字段只在低置信时出现，不污染正常条目。
        quote_chars = len(_normalize_no_space(quote))
        if quote_chars < SHORT_QUOTE_CHARS:
            entry["quote_confidence"] = "low"
            entry["quote_chars"] = quote_chars
            notes.append(
                f"子问题「{label}」的 source_quote 仅 {quote_chars} 个非空白字符"
                f"（低置信阈值 {SHORT_QUOTE_CHARS}），虽逐字命中原文但定位力弱，"
                f"已按低置信接受，请人工复核出处：{quote[:40]!r}")

        kept.append(entry)

    return kept, notes, dropped


# ---------------------------------------------------------------- 入口
def parse_problem(problem_text: str, *, runner=None, profile_id: str | None = None,
                  actor: str = "archivist",
                  db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """题面 → 结构化子问题（三要素 + 原文引用），并做确定性后校验。

    返回 `{subproblems, open_questions, summary, dropped, source_chars, prompt_version,
    problem}`；`problem` 为**未截断**的原始题面（供档案 `problem` 字段落库）。
    `profile_id` / `actor` 只为调用方可读性保留（本函数不写库，写库见
    `write_profile_from_parse`）。
    """
    original = str(problem_text or "")
    source_chars = len(original)
    text = original
    open_questions: list[str] = []
    if source_chars > PROBLEM_TEXT_LIMIT:
        text = original[:PROBLEM_TEXT_LIMIT]
        open_questions.append(
            f"题面过长，仅解析前 {PROBLEM_TEXT_LIMIT} 字符（原文 {source_chars} 字符），"
            f"其余部分未参与抽取，需人工确认是否补解析")

    runner = runner or make_runner(db_path=db_path)
    raw = runner.run({"problem_text": text})
    if not isinstance(raw, dict):
        raw = {}
    raw_subs = raw.get("subproblems")
    if not isinstance(raw_subs, list):
        raw_subs = []
    raw_open = raw.get("open_questions")
    if not isinstance(raw_open, list):
        raw_open = [str(raw_open)] if raw_open else []

    subs, notes, dropped = _verify_against_source(text, raw_subs)
    open_questions.extend(str(q) for q in raw_open if str(q).strip())
    open_questions.extend(notes)

    summary = _as_text(raw.get("summary"))
    if not summary:
        summary = (f"题面共 {source_chars} 字符，解析出 {len(subs)} 个子问题、"
                   f"{len(open_questions)} 条待确认项（summary 由系统按结果计数生成）")

    return {
        "subproblems": subs,
        "open_questions": open_questions,
        "summary": summary,
        "dropped": dropped,
        # 供导出闸（FR-12 闸 d）与人工抽检：低置信引用的条数，**不参与是否放行**
        "low_confidence_quotes": sum(1 for s in subs
                                     if s.get("quote_confidence") == "low"),
        "source_chars": source_chars,
        "prompt_version": ARCHIVIST_PROMPT_VERSION,
        "problem": original,
    }


def write_profile_from_parse(parsed: dict, profile_id: str, actor: str = "archivist",
                             db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """把解析结果写成档案 **draft** 版本（不 adopt：采纳是人工卡点）。"""
    from ..tools import profile_tool

    patch = {
        "problem": parsed.get("problem", ""),
        "subproblems": parsed.get("subproblems", []),
        "open_questions": parsed.get("open_questions", []),
        "summary": parsed.get("summary", ""),
        "parse_meta": {
            "prompt_version": parsed.get("prompt_version", ARCHIVIST_PROMPT_VERSION),
            "dropped": int(parsed.get("dropped", 0)),
            "source_chars": int(parsed.get("source_chars", 0)),
            "low_confidence_quotes": int(parsed.get("low_confidence_quotes", 0)),
        },
    }
    version_id = profile_tool.write_version(profile_id, patch, actor, db_path=db_path)
    return {
        "profile_id": profile_id,
        "version_id": version_id,
        "status": "draft",
        "subproblems": len(patch["subproblems"]),
        "open_questions": len(patch["open_questions"]),
        "dropped": patch["parse_meta"]["dropped"],
    }
