"""planner 角色：FR-3 方案卡片（题面 → ≥3 个候选方法论 → 确定性反造假 → 落 artifacts）。

**为什么是「3 个候选」而不是 1 个结论**：项目总原则是「模型只出候选、判定由人做」
（`docs/REMAINING_PLAN.md` §2.2 C1）。单结论会诱导直接采用，故本模块把「≥3 个**可执行**
候选方法」做成**硬校验**，不是提示词祈祷：
- `tasks` 少于 `MIN_CANDIDATES` 条 → `LLMOutputSyntaxError`；
- 任一条缺 `id`/`title`/`method`/`fit`/`unfit`/`data_needed`/`acceptance`（或为空）→ 同上；
- 两条候选 `method` 重名、或方法名是占位（「其他方法」「待定」「视情况而定」「综合方法」…）
  或带评价词（「推荐」「首选」）→ 同上（占位词表与 prompt 禁令逐条对齐，有专门用例锁住）。

**反造假是代码保证**（照 FR-1 `archivist._verify_against_source` 的思路，**不 import 其私有
函数**，在本模块内自实现）：`_verify_no_fabricated_numbers` 按分句切分候选文本，把「题面里
没出现过的数字」所在分句剔除，并把每次剔除记进返回的第二值（被剔除清单）。`T1`/`T2` 这类
条目编号作为**整体记号**在字段内被抹掉后再抽数字——既不让「见 T1」被误剔，也**不**把 `1/2/3`
变成全局合法数字。

**清洗之后必须复验**（D1，顺序不能反）：`method` 被剔空的候选直接移除，移除后不足 3 条 →
`LLMOutputSyntaxError`；`fit`/`unfit`/`data_needed`/`acceptance` 被剔空 → 候选保留但记
`incomplete_fields`，汇总到返回值的 `valid=false` 上，交 PLAN gate 决定是否重采样。

**题面为空时反造假失效必须可见**：返回 `fabrication_check="skipped"`，不允许编造数字靠
「漏传题面」蒙混成 `done`。`valid`（信息全不全）与 `ready_for_gate`（能不能交给 PLAN 卡点，
`valid and fabrication_check == "done"`）是两个概念，gate 应读后者。

**`id` 必须以字母开头**（`T1`/`A`…）：纯数字 id 与数据数字天然不可区分，会让「整体抹掉编号」
的反造假机制被架空（`id="3"` 时编造的「3 万吨」会被当成编号抹掉），故纯数字 id 直接判输出无效。

**不新增表、不改 `schema.sql`**：产物登记走 `store/artifacts.py::record_artifact(kind="plan")`，
全量 JSON 落 `artifacts/plan/<run_id>.json`。

**不接线 coordinator**：PLAN 人工卡点属后续轮次，本模块只到「能产出并落盘」。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path

from ..store.artifacts import record_artifact
from ..store.db import DEFAULT_DB_PATH
from .base import LLMOutputSyntaxError, RoleRunner, strip_code_fence

PLANNER_PROMPT_VERSION = "planner@0.2.0"

# RoleRunner 的宽松闸（只保证拿到 tasks）；**严格闸**（≥3 候选 / 字段齐备 / 方法不重名）
# 由 `parse_plan` 做，失败一律 `LLMOutputSyntaxError`（复用 `base.py` 既有异常，不新造）。
PLANNER_SCHEMA = {"required": ["tasks"]}

# 三个候选方法的硬下限（「模型只出候选、判定由人做」在数量上的落地）
MIN_CANDIDATES = 3

# 字段名冻结（README §1-B1）：任一为空即视为该条候选无效
TASK_REQUIRED_FIELDS = ("id", "title", "method", "fit", "unfit", "data_needed", "acceptance")

# 参与数字反造假扫描的**文本**字段（`id` 不扫描：T1/T2 是条目编号，不是数据）
TEXT_FIELDS = ("title", "method", "fit", "unfit", "data_needed", "acceptance")

# 全量 plan JSON 的落地目录（相对仓库根）
REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "plan"

_WS_RE = re.compile(r"\s+")
# 条目编号形状：**必须以字母开头**（R2-1）。纯数字 id 与数据数字不可区分，
# 会让 `_id_pattern` 把编造数字当编号整体抹掉 → 直接判输出无效。
_ID_SHAPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# 数字 token（含小数与百分号）；NFKC 后全角数字/％ 已归一为半角
_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
# 分句分隔符（保留在切分结果中，便于原样重组）
_CLAUSE_SEP_RE = re.compile(r"([，,；;。！？!?\n])")
_CLAUSE_TAIL = "，,；;。、 \t"

# 占位方法名（规范化后比对）：这些不是「可执行的方法」，是凑数。
# **口径**：prompt 里显式禁止什么，本表就必须收什么（`tests/test_fr3_plan.py` 用一条
# 一致性用例锁住「prompt 禁令 ↔ 本表」——只改 prompt 不改本表会红）。
_PLACEHOLDER_METHODS = frozenset({
    # 泛化指代（不是方法名）
    "其他", "其他方法", "其它", "其它方法", "其余方法", "剩余方法", "以上皆可", "均可",
    "任意", "多种方法", "无", "暂无", "未知", "待补充",
    # 推诿式表述（prompt 第 9 行明文禁止）
    "综合方法", "综合法", "综合方案", "综合处理", "视情况而定", "视情况", "视条件而定", "看情况",
    "灵活选择", "灵活处理",
    # 未定
    "待定", "暂定", "待选", "n/a", "na", "tbd", "todo", "other", "others",
})

# 评价词（prompt 第 43 行：`method` 只写方法名本身，不要写「推荐」「首选」等评价词）。
# method 里出现这些词 → 不是方法名，是结论/排序。
_EVAL_WORDS = ("推荐", "首选", "建议采用", "最佳", "更优", "最优选", "强烈建议")


def make_runner(**kwargs) -> RoleRunner:
    return RoleRunner("planner", PLANNER_PROMPT_VERSION, PLANNER_SCHEMA, **kwargs)


# ---------------------------------------------------------------- 归一化 / 数字口径
def _normalize(text: object) -> str:
    """比较口径：NFKC（全角→半角）+ 连续空白折叠成单空格 + 去首尾空白。

    **不做**模糊匹配（不改字、不同义词、不做编辑距离）：除字符宽度与空白外，
    数字必须按原样出现才算「来自题面」。
    """
    t = unicodedata.normalize("NFKC", str(text or ""))
    return _WS_RE.sub(" ", t).strip()


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


def _method_key(method: str) -> str:
    """方法名比对键：NFKC + 小写 + 去全部空白 + 去尾部标点。"""
    key = re.sub(r"\s+", "", _normalize(method)).lower()
    return key.rstrip("。.．,，;；")


# ---------------------------------------------------------------- 反造假（核心）
def _id_pattern(ids: tuple[str, ...]) -> re.Pattern | None:
    """把条目编号（`T1`/`A`…）编译成「整体匹配」的正则：长 id 优先、前后不粘数字。

    D3：编号**整体**被视为合法记号（所以「见 T1」不会被当成数字 `1` 而误剔），但其
    数字部分**不并入全局允许集**（否则 `T1/T2/T3` 会让 `1/2/3` 在任何字段里都合法——
    验收方用 `id=A/B/C` 的对照实验证明过这条污染路径）。

    R2-1：**纯数字 id 一律跳过**，不参与抹除——否则 `id="3"` 会把任何独立的 `3`
    （如编造的「3 万吨」）当整体记号抹掉，反造假形同虚设。
    """
    candidates = {i for i in ids if i and not i.strip().isdigit()}
    parts = [re.escape(_normalize(i)) for i in sorted(candidates, key=len, reverse=True)]
    if not parts:
        return None
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(parts) + r")(?![0-9])",
                      re.IGNORECASE)


def _numbers_ignoring_ids(text: str, id_re: re.Pattern | None) -> list[str]:
    """抽数字前先把条目编号整体抹掉（`T1` 不再是数字 `1`）。"""
    t = _normalize(text)
    if id_re is not None:
        t = id_re.sub(" ", t)
    return _NUM_RE.findall(t)


def _strip_bad_clauses(text: str, allowed: set[str], id_re: re.Pattern | None,
                       task_id: str, field: str, dropped: list[str]) -> str:
    """剔除含「题面没有的数字」的分句，并把每个越界数字记进 `dropped`。

    分句口径：按 `，,；;。！？!?` 与换行切分（分隔符原样保留）；只剔命中越界数字的
    **那个分句**，同字段其它分句保留——避免为了一个编造数字把整条候选抹掉。
    """
    parts = _CLAUSE_SEP_RE.split(text)
    kept: list[str] = []
    for i in range(0, len(parts), 2):
        clause = parts[i]
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        bad = [n for n in _numbers_ignoring_ids(clause, id_re) if n not in allowed]
        if bad:
            dropped.append(
                f"{task_id}.{field} 剔除分句「{clause.strip()}」："
                f"数字 {'、'.join(bad)} 未在题面出现")
        else:
            kept.append(clause + sep)
    return "".join(kept).strip().rstrip(_CLAUSE_TAIL)


def _verify_no_fabricated_numbers(tasks: list[dict],
                                  problem_text: str) -> tuple[list[dict], list[str]]:
    """候选条目里的数字若不在题面 → 剔除该分句并记账。

    返回 `(清洗后的 tasks, 被剔除清单)`。允许集**只有**题面里出现过的数字；各条 `id`
    （`T1`/`T2`）作为**整体记号**在字段内被抹掉后再抽数字（D3：id 的数字不并入允许集）。
    **题面为空时不做剔除**（没有比对基准，全剔会把所有数字当编造——那是错杀）。

    同时给每条打 `incomplete_fields`：被剔空的字段名列表（D1）。**不判断候选是否有效**
    ——那一步在 `parse_plan` 里、清洗之后做（顺序不能反）。
    """
    allowed: set[str] = set(_numbers(problem_text))
    id_re = _id_pattern(tuple(_as_text(t.get("id")) for t in tasks))

    dropped: list[str] = []
    cleaned: list[dict] = []
    for task in tasks:
        tid = _as_text(task.get("id")) or "?"
        item = dict(task)
        incomplete: list[str] = []
        for field in TEXT_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            stripped = _strip_bad_clauses(value, allowed, id_re, tid, field, dropped)
            item[field] = stripped
            if not stripped:
                # 整段被剔空：留空**不补齐**（宁可露个洞，也不让系统自己编一个）
                incomplete.append(field)
                dropped.append(f"{tid}.{field} 全部被剔除，需人工补写（系统不代笔）")
        item["incomplete_fields"] = incomplete
        cleaned.append(item)
    return cleaned, dropped


# ---------------------------------------------------------------- schema 校验
def _load_plan(plan_text, *, runner=None, problem_text: str = "") -> dict:
    """把入参变成 dict：`str` 走 JSON 解析（容忍代码围栏），`dict` 直接用。

    `plan_text` 为空且给了 `runner` 时，用 runner 现取一次（这是 `parse_plan` 保留
    `runner` 形参的唯一用途）；两者都没有 → `LLMOutputSyntaxError`。
    """
    if isinstance(plan_text, dict):
        return plan_text
    text = str(plan_text or "").strip()
    if not text:
        if runner is not None:
            obj = runner.run({"problem_text": problem_text})
            return obj if isinstance(obj, dict) else {}
        raise LLMOutputSyntaxError("plan 为空：既没有 JSON 文本，也没有可兜底的 runner")
    try:
        obj = json.loads(strip_code_fence(text), strict=False)
    except json.JSONDecodeError as exc:
        raise LLMOutputSyntaxError(f"plan 不是合法 JSON：{exc}") from exc
    if not isinstance(obj, dict):
        raise LLMOutputSyntaxError(f"plan 不是 JSON 对象（实际 {type(obj).__name__}）")
    return obj


def _check_count(tasks: list[dict], *, stage: str = "") -> None:
    """候选数量闸（挡「1 个结论」）。`stage` 只用于报错措辞（清洗前 / 清洗后）。"""
    if len(tasks) < MIN_CANDIDATES:
        ids = [_as_text(t.get("id")) or f"#{i}" for i, t in enumerate(tasks, 1)]
        raise LLMOutputSyntaxError(
            f"候选方法不足{stage}：需要 ≥{MIN_CANDIDATES} 个可执行候选，实际 {len(tasks)} 条"
            f"（{'、'.join(ids) if ids else '空'}）。方案卡片必须给多个候选，不给单一结论")


def _check_method_name(task: dict, idx: int) -> str:
    """单个 `method` 的可用性：不许占位、不许带评价词。返回比对键。"""
    tid = _as_text(task.get("id")) or f"#{idx}"
    method = _as_text(task.get("method"))
    key = _method_key(method)
    if key in _PLACEHOLDER_METHODS:
        raise LLMOutputSyntaxError(
            f"{tid} 的 method={method!r} 是占位名，不是可执行方法："
            f"三个候选都必须是能落地的方法，不许用「其他方法」「视情况而定」凑数")
    hit = [w for w in _EVAL_WORDS if w in method]
    if hit:
        raise LLMOutputSyntaxError(
            f"{tid} 的 method={method!r} 含评价词 {'、'.join(hit)}：`method` 只写方法名本身，"
            f"「推荐/首选」这类结论必须由人做，模型只给候选")
    return key


def _check_methods(tasks: list[dict]) -> None:
    """方法名闸：占位名 + 评价词 + 重名（清洗前后各跑一次，D1）。"""
    seen: dict[str, str] = {}
    for idx, task in enumerate(tasks, 1):
        tid = _as_text(task.get("id")) or f"#{idx}"
        key = _check_method_name(task, idx)
        if key in seen:
            raise LLMOutputSyntaxError(
                f"{tid} 与 {seen[key]} 的 method 重名（{_as_text(task.get('method'))!r}）："
                f"重名等于少给候选，需给出 {MIN_CANDIDATES} 个**不同**方法")
        seen[key] = tid


def _check_candidates(tasks: list[dict]) -> None:
    """数量 / 方法名三道闸（挡「1 个推荐 + 2 个凑数」）。"""
    _check_count(tasks)
    _check_methods(tasks)


def _revalidate_after_clean(tasks: list[dict], dropped: list[str]) -> list[dict]:
    """**清洗之后**的复验（D1：顺序不能反——先清洗，再判有效）。

    规则（按验收方裁定的分层，不一刀切）：
    - `method` 被剔成空串 → 该候选**直接无效**（方法名没了，这条卡片没有意义），移除；
    - 其余字段被剔空 → 候选仍有效，字段名已在 `incomplete_fields` 里（不在此处判死）；
    - 有效候选 < `MIN_CANDIDATES` → 抛 `LLMOutputSyntaxError`（不静默降级成 2 条）；
    - 清洗后两条 `method` 同名 / 占位 / 带评价词 → 抛（候选失去区分度）。
    """
    valid: list[dict] = []
    removed: list[str] = []
    for idx, task in enumerate(tasks, 1):
        tid = _as_text(task.get("id")) or f"#{idx}"
        if not _as_text(task.get("method")):
            removed.append(tid)
            continue
        valid.append(task)

    # 先算完保留集再记账：这样每条记录里的「剩余 N 条」是最终值，不是中间值
    for tid in removed:
        dropped.append(
            f"{tid}.method 清洗后为空 → 该候选无效，已移除（保留 {len(valid)} 条候选）")

    _check_count(valid, stage="（数字清洗后）")
    _check_methods(valid)
    return valid


def _validate_tasks(raw: dict) -> list[dict]:
    """严格 schema 校验：不合格一律 `LLMOutputSyntaxError`（不静默补字段）。"""
    if "tasks" not in raw:
        raise LLMOutputSyntaxError(
            f"plan 缺少必需字段：['tasks']（实际键：{sorted(raw)}）")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        raise LLMOutputSyntaxError(f"tasks 必须是数组（实际 {type(tasks).__name__}）")

    cleaned: list[dict] = []
    for idx, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise LLMOutputSyntaxError(f"tasks[{idx}] 不是 JSON 对象：{task!r}")
        missing = [f for f in TASK_REQUIRED_FIELDS if not _as_text(task.get(f))]
        if missing:
            raise LLMOutputSyntaxError(
                f"tasks[{idx}]（id={task.get('id')!r}）缺字段或为空：{missing}。"
                f"每条候选必须带 id/title/method/fit/unfit/data_needed/acceptance")
        tid = _as_text(task.get("id"))
        if not _ID_SHAPE_RE.match(tid):
            # R2-1：纯数字 id 与数据数字天然不可区分，留着就是给反造假留绕过口子
            # （`id="3"` 会把编造的「3 万吨」当编号整体抹掉）
            raise LLMOutputSyntaxError(
                f"tasks[{idx}] 的 id={task.get('id')!r} 不合法：id 必须以**字母开头**，"
                f"只含字母/数字/下划线/连字符（如 T1）。纯数字 id 会与数据数字混淆、"
                f"架空数字反造假，故判输出无效")
        item = dict(task)
        for field in TASK_REQUIRED_FIELDS:
            item[field] = _as_text(task.get(field))
        cleaned.append(item)

    _check_candidates(cleaned)
    return cleaned


# ---------------------------------------------------------------- 入口：解析
def parse_plan(plan_text: str, *, problem_text: str = "", runner=None) -> dict:
    """模型输出 → 校验后的方案卡片（≥3 个候选 + 反造假 + 清洗后复验）。

    `plan_text` 可为 JSON 字符串（容忍 Markdown 围栏）或已解析的 dict。

    **顺序不可反**（D1）：结构校验（形状/数量/方法名）→ 数字清洗 → **复验有效候选**。
    清洗把 `method` 剔空的候选直接移除；移除后不足 `MIN_CANDIDATES` → `LLMOutputSyntaxError`
    （不静默降级成 2 条）。其余字段被剔空 → 候选仍有效，但记进该条 `incomplete_fields`。

    返回 `{tasks, open_questions, dropped, candidate_count, incomplete_fields, valid,
    ready_for_gate, fabrication_check, prompt_version, source_chars}`：
    - `dropped`：被剔除清单（每条含 task id/字段/分句/越界数字），供人工复核；
    - `incomplete_fields`：**汇总**的不完整字段，形如 `["T2.data_needed", "T3.fit"]`；
    - `valid`：**只回答「信息全不全」** —— `incomplete_fields` 为空才 `True`（R2-2：
      不要把反造假是否执行混进来，否则两个概念互相污染）；
    - `ready_for_gate`：**只回答「能不能交给 PLAN 卡点用」** = `valid and
      fabrication_check == "done"`。gate 的 accept 分支应读这个字段，而不是 `valid`；
    - `fabrication_check`：`"done" | "skipped"`（题面为空 → `skipped`，反造假不生效，
      调用方必须看得见，不能让编造数字靠「漏传题面」蒙混）。
    """
    raw = _load_plan(plan_text, runner=runner, problem_text=problem_text)
    tasks = _validate_tasks(raw)

    # `open_questions` 允许缺失（不得给出则默认空数组）；给错类型时按单条字符串收敛
    raw_open = raw.get("open_questions")
    if isinstance(raw_open, list):
        open_questions = [str(q).strip() for q in raw_open if str(q).strip()]
    elif raw_open:
        open_questions = [str(raw_open).strip()]
    else:
        open_questions = []

    problem = str(problem_text or "")
    if problem.strip():
        tasks, dropped = _verify_no_fabricated_numbers(tasks, problem)
        fabrication_check = "done"
    else:
        dropped = []
        fabrication_check = "skipped"
        for task in tasks:
            task["incomplete_fields"] = []
        open_questions.append(
            "未提供题面：数字反造假校验已跳过（fabrication_check=skipped，没有比对基准），"
            "各候选的 method 一律按「模型建议」对待（from_archive=false）")

    # 清洗之后才判有效候选（D1）：method 空 → 移除；不足 3 条 → 抛
    tasks = _revalidate_after_clean(tasks, dropped)

    # C2：方法名若能在题面里定位 → 有题面依据；否则如实标「模型建议」（不冒充题面依据）
    source = _normalize(problem)
    for task in tasks:
        method = _normalize(task.get("method"))
        task["from_archive"] = bool(method) and bool(source) and method in source

    incomplete = [f"{_as_text(t.get('id')) or '?'}.{field}"
                  for t in tasks for field in t.get("incomplete_fields") or []]
    valid = not incomplete

    return {
        "tasks": tasks,
        "open_questions": open_questions,
        "dropped": dropped,
        "candidate_count": len(tasks),
        "incomplete_fields": incomplete,
        # `valid`：信息全不全（只看字段完整性）
        "valid": valid,
        # `ready_for_gate`：能不能交给 PLAN 卡点用（完整性 **且** 反造假真的执行过）
        "ready_for_gate": valid and fabrication_check == "done",
        "fabrication_check": fabrication_check,
        "prompt_version": PLANNER_PROMPT_VERSION,
        "source_chars": len(problem),
    }


# ---------------------------------------------------------------- 入口：产出并落盘
def _model_tag(runner) -> str:
    """尽力取 `<provider>/<model>` 标签；取不到返回空串（观测不阻断主流程）。"""
    try:
        from .base import provider_models

        return f"{runner.provider}/{provider_models(runner.config)['generation']}"
    except Exception:  # noqa: BLE001  假 runner / 缺配置时不阻断
        return ""


def generate_plan(problem_text: str, *, runner=None, profile_id: str | None = None,
                  db_path: str | Path = DEFAULT_DB_PATH,
                  out_dir: str | Path = PLAN_ARTIFACT_DIR,
                  run_id: str | None = None) -> dict:
    """题面 → 方案卡片，并把全量 JSON 落 `artifacts/plan/<run_id>.json` + 登记 `artifacts`。

    - 只到「产出并落盘」：**不**自动流入 solve（是否消费由 PLAN 人工卡点裁定）；
    - 落盘失败不掩盖校验失败：先校验、后写盘（`LLMOutputSyntaxError` 不会留下半成品）；
    - `run_id` 缺省取 `runner.run_id`，再缺省 `uuid4().hex`（文件名与登记行同源）。
    """
    runner = runner or make_runner(db_path=db_path)
    problem = str(problem_text or "")
    raw = runner.run({"problem_text": problem})
    plan = parse_plan(raw, problem_text=problem)

    rid = run_id or getattr(runner, "run_id", None) or uuid.uuid4().hex
    plan["run_id"] = rid
    plan["profile_id"] = profile_id

    plan_dir = Path(out_dir)
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / f"{rid}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    try:
        rel_path = plan_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:          # 自定义 out_dir 在仓库外（测试用 tmp_path）：登记绝对路径
        rel_path = plan_path.resolve().as_posix()

    plan["artifact_id"] = record_artifact(
        "plan", path=rel_path, profile_id=profile_id, producer="planner",
        prompt_version=PLANNER_PROMPT_VERSION, model_tag=_model_tag(runner),
        input_hash=hashlib.sha256(problem.encode("utf-8")).hexdigest()[:16],
        db_path=db_path)
    plan["plan_path"] = rel_path
    return plan
