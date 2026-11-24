"""FR-8 进度与时间管理（最小形态）：倒计时 + 不做清单 + 无产出预警。

**纯确定性、零 LLM、只读库**：本模块不做任何推断式生成，全部结论由
`events` / `artifacts` 的**行数、时间戳、payload** 直接推出（不占推理额度）；
不写库（连接一律 `file:…?mode=ro`）、不联网、不读环境变量。

口径（与《需求说明书》FR-8 对齐，逐条落常量）：

- **产出时间**取 `artifacts.ts` 的最大值（`artifacts` 是"产物登记"，这才是"产出"）；
  若库里有事件但**一条产物都没有**，退化为 `events.ts` 最大值（记 `last_output_source="event"`，
  语义是"最后活动"，此时 `idle_hours` 仍是"多久没有新产出"的上界，不会把空库当成空闲）。
- `events` 表**没有 deadline / stage 列**（见 `schema.sql`）：`deadline` 只能由调用方传入；
  `stage` 从**最后一条事件**的 `payload` 里取，键序 `stage` → `to`（`coordinator.transition`
  用 `to`，`hitl.gate_paused` 用 `stage`），两键都没有 → `None`，**不猜**。
- 阈值：`idle_hours >= 6.0` → `IDLE_NO_OUTPUT`；`remaining_hours <= 6.0` → `min_viable`；
  `remaining_hours <= 4.0` → `not_do_list`。`deadline is None` 时**不触发**后两条，
  且返回 `deadline_missing=True`（"没给截止时间" ≠ "时间充裕"）。
- **每条预警必须带非空 `action`**（可执行降级动作）；缺 `action` 的计入 `invalid_warnings`，
  并拉低 `actionable_ratio`（目标：无效预警 0 条）。
- 库文件不存在 → `FileNotFoundError`（不静默当空库）；库存在但表缺失 → 按 0 行处理。
- **`profile_id` 对两张表同时生效**：`artifacts.profile_id` 走列过滤，`events` 走
  `json_valid(payload) AND CAST(json_extract(payload,'$.profile_id') AS TEXT)=CAST(? AS TEXT)`
  （两侧文本比，数字 `1` 与字符串 `"1"` 视为同一档案）。计数、最大 `ts`、最后一条事件
  （`stage`）**共用同一 WHERE**（`_profile_where`），避免"计数按全库、stage 按档案"的
  不对称 —— 那会让零事件档案查出全库行数（gk-r23 S1）。
- **过滤后为空 ≠ 空库**：现网 `events.payload` 与 `artifacts.profile_id` 普遍未落值，
  按档案过滤必然为空。此时若全库有行，返回 `PROFILE_FILTER_EMPTY`（而**不是** `NO_RECORD`），
  并给出 `unfiltered_event_count` / `unfiltered_artifact_count` 供调用方识别口径不成立。
- 相对 `db_path` 合法：`_connect_ro` 内部 `expanduser().resolve()` 后再拼 `file:` URI
  （`Path.as_uri()` 只接受绝对路径，CLI 默认值恰是相对路径 `data/mm.db`，gk-r23 B1）。

时间一律按 UTC 解释：`Z` 后缀 / `+08:00` 偏移 / 无时区的 naive 值（视为 UTC）都可注入。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("data/mm.db")

# ---- 阈值（小时，常量即口径）----
IDLE_THRESHOLD_HOURS = 6.0
MIN_VIABLE_AT_HOURS = 6.0
NOT_DO_AT_HOURS = 4.0

# ---- 最短可交版本 ----
# 诚实性口径（gk-r23 §4 溯源核对结论）：**触发机制**（前 6 h 转最短可交版本）出自
# 《需求说明书》FR-8；**清单 7 章 + 3 图的具体条目，文档从未逐条枚举**，由本实现拟定。
# 不许把"拟定清单"标注成"文档原文"——这是溯源失实（清单无据可接受，冒称有据不可接受）。
MIN_VIABLE_SOURCE = (
    "触发机制：需求说明书 FR-8（前 6 h 转最短可交版本）；"
    "清单条目为本实现拟定，文档未逐条枚举"
)
MIN_VIABLE_CHAPTERS = (
    "论文摘要（P0：结论 + 关键数字）",
    "问题重述与分析（P0）",
    "模型假设与符号说明（P0）",
    "模型建立与求解（P0：含求解结果表）",
    "结果分析与检验（含误差/灵敏度）",
    "模型评价与推广（可压缩为 1 段）",
    "参考文献与附录（代码清单）",
)
MIN_VIABLE_FIGURES = (
    "数据概览图（P0）",
    "模型拟合/预测主结果图（P0）",
    "误差或灵敏度分析图（P0）",
)

# ---- 不做清单：前 4 h 触发，全部为"可砍"项，每条给出何时生效 ----
# 来源同 `MIN_VIABLE_SOURCE`：触发机制（前 4 h 触发不做清单）出自 FR-8；4 条条目为本实现拟定。
NOT_DO_SOURCE = "触发机制：需求说明书 FR-8（前 4 h 触发不做清单）；条目为本实现拟定"
NOT_DO_ITEMS = (
    {"item": "非 P0 章节的扩展讨论（模型推广、改进方向长文）",
     "why": "长文扩展边际收益最低，评审不额外加分",
     "when": "remaining<=4h"},
    {"item": "额外的敏感性/稳健性实验（已有一组即可）",
     "why": "重跑成本高，且不改变主结论",
     "when": "remaining<=4h"},
    {"item": "排版美化与多轮文字润色（字体、页眉页脚、措辞迭代）",
     "why": "不影响可交性，交付前只做一次通读",
     "when": "remaining<=4h"},
    {"item": "附录代码的整理与注释补全",
     "why": "评委按可运行性看，原样贴出即可",
     "when": "remaining<=4h"},
)


# ---------------------------------------------------------------- 时间工具

def _parse_ts(value) -> datetime:
    """把 ISO8601 字符串 / `datetime` 解析为**带时区**的 UTC datetime。

    支持 `Z` 后缀、`+08:00` 偏移、无时区 naive（按 UTC 处理）。空值抛 `ValueError`。
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("空时间字符串")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise TypeError(f"不支持的时间类型：{type(value).__name__}")
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _fmt_ts(dt: datetime) -> str:
    """输出与 `store.db.utcnow()` 同形：`YYYY-MM-DDTHH:MM:SS.mmmZ`（毫秒保留）。"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _hours_between(later: datetime, earlier: datetime) -> float:
    """`later - earlier` 的小时数（保留 3 位；负值表示 earlier 在未来）。"""
    return round((later - earlier).total_seconds() / 3600.0, 3)


# ---------------------------------------------------------------- 库读取（只读）

# `profile_id` 对 `events` 的唯一过滤口径（`events` 无 profile_id 列，只能走 payload）。
# **计数、最大 ts、最后一条事件三处必须共用**，否则会出现"计数撒谎"：
# 查一个零事件档案却回全库行数 → `NO_RECORD` 不触发 → 空档案静默全清（gk-r23 S1）。
#
# 两侧都 **CAST 成 TEXT** 再比：`json_extract` 保留 JSON 原生类型，某条事件写
# `"profile_id": 1`（数字）而调用方传 `"1"` 时，原写法 `= ?` 会类型不匹配而静默漏掉
# ——同档案在 events 与 artifacts（TEXT 列）两侧判定不一致（gk-r23 第 2 轮 C5）。
_PROFILE_EVENT_WHERE = (
    "json_valid(payload)"
    " AND CAST(json_extract(payload, '$.profile_id') AS TEXT) = CAST(? AS TEXT)"
)


def _profile_where(table: str, profile_id) -> tuple[str, tuple]:
    """返回 `(WHERE 子句, 参数)`；无过滤时 `("", ())`。两张表的过滤口径都只在这里定义。

    `profile_id` 允许 `str` 或 `int`（`0` 也算有效值）；两侧统一按文本比较。
    """
    if profile_id is None or str(profile_id) == "":
        return "", ()
    if table == "events":
        return f" WHERE {_PROFILE_EVENT_WHERE}", (profile_id,)
    return " WHERE profile_id = ?", (profile_id,)


def _connect_ro(db_path: str | Path) -> sqlite3.Connection:
    """以 `mode=ro` 打开库：**任何写语句都会被 SQLite 拒绝**（只读是硬保证，不是约定）。

    `Path.as_uri()` 只接受**绝对**路径，而 `DEFAULT_DB_PATH` / CLI `--db` 默认值都是相对路径
    （如 `data/mm.db`）→ 必须先 `expanduser().resolve()` 再拼 URI，否则开箱即崩
    （`ValueError: relative path can't be expressed as a file URI`，gk-r23 B1）。
    """
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"数据库不存在：{path}（先跑 cli intake/solve 建库）")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str, profile_id: str | int | None) -> int:
    """行数；表缺失按 0（空库容错）。`profile_id` 对两张表都生效（口径见 `_profile_where`）。"""
    if not _table_exists(conn, table):
        return 0
    where, params = _profile_where(table, profile_id)
    return int(conn.execute(
        f"SELECT COUNT(*) AS n FROM {table}{where}", params).fetchone()["n"])


def _max_ts(conn: sqlite3.Connection, table: str, profile_id: str | int | None) -> str | None:
    """该表该档案的最大 `ts`（TEXT ISO8601，字典序即时间序）；表缺失/空表 → `None`。"""
    if not _table_exists(conn, table):
        return None
    where, params = _profile_where(table, profile_id)
    row = conn.execute(f"SELECT MAX(ts) AS ts FROM {table}{where}", params).fetchone()
    return (row["ts"] or None) if row is not None else None


def _last_event(conn: sqlite3.Connection, profile_id: str | int | None) -> dict | None:
    """最后一条事件（`id` 最大；`ts` 相同时 `id` 才是真正的写入顺序）——**仅供取 `stage`**。

    注意：本函数**不**用于"最后活动时刻"，那个语义必须用 `_max_ts(conn,"events",…)`：
    `id` 与 `ts` 可能逆序（补录/时钟偏移），取 last-by-id 的 `ts` 会算错 `idle_hours`（gk-r23 G2）。
    """
    if not _table_exists(conn, "events"):
        return None
    where, params = _profile_where("events", profile_id)
    row = conn.execute(
        f"SELECT id, ts, event_type, payload FROM events{where}"
        " ORDER BY id DESC LIMIT 1", params).fetchone()
    return dict(row) if row is not None else None


def _load_payload(raw) -> dict:
    """`payload` 是 JSON 文本；解析失败 / 非对象一律当空对象（字段走缺失分支）。"""
    try:
        obj = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _stage_of(event: dict | None) -> str | None:
    """从事件 payload 取阶段：键序 `stage` → `to`；没有就 `None`（不猜、不填默认值）。"""
    if not event:
        return None
    payload = _load_payload(event.get("payload"))
    for key in ("stage", "to"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


# ---------------------------------------------------------------- 预警

def _warning(code: str, message: str, action: str, level: str = "warn") -> dict:
    return {"code": code, "level": level, "message": message, "action": action}


def _finalize_warnings(warnings: list[dict]) -> dict:
    """统计预警可执行性（**唯一**计算 `actionable_ratio` 的地方）。

    `actionable_ratio = 带非空 action 的预警数 / 预警总数`（无预警时定义 1.0，
    避免"零预警"被误判成"全是无效预警"）；缺 action 的同时计入 `invalid_warnings`。
    """
    total = len(warnings)
    invalid = sum(1 for w in warnings if not str(w.get("action") or "").strip())
    ratio = 1.0 if total == 0 else round((total - invalid) / total, 4)
    return {"warnings": warnings, "actionable_ratio": ratio, "invalid_warnings": invalid}


def _build_warnings(*, event_count: int, artifact_count: int,
                    idle_hours: float | None, remaining_hours: float | None,
                    deadline_missing: bool, profile_id=None,
                    unfiltered_event_count: int = 0,
                    unfiltered_artifact_count: int = 0) -> list[dict]:
    """按阈值生成预警（顺序固定：NO_RECORD / PROFILE_FILTER_EMPTY → IDLE → …）。

    `NO_RECORD` 与 `PROFILE_FILTER_EMPTY` **互斥**：过滤后为空但全库有行时，根因是
    "写入方没记 profile_id"，不能报成"库里没有任何事件与产物"（反向撒谎）。
    """
    out: list[dict] = []
    filtered_empty = event_count == 0 and artifact_count == 0
    unfiltered_has = unfiltered_event_count > 0 or unfiltered_artifact_count > 0
    if filtered_empty and profile_id is not None and str(profile_id) != "" and unfiltered_has:
        out.append(_warning(
            "PROFILE_FILTER_EMPTY",
            f"档案 {profile_id!r} 在库中无记录，但全库有 {unfiltered_event_count} 条事件 / "
            f"{unfiltered_artifact_count} 条产物 —— 大概率是写入时未记录 profile_id，"
            "该过滤口径对现网数据不成立（不是真的空库）。",
            "先用不带 profile_id 的全库口径（profile_id=None）看进度；"
            "要让过滤生效，需写入方在 events.payload.profile_id 与 artifacts.profile_id 落值。"))
    elif filtered_empty:
        out.append(_warning(
            "NO_RECORD",
            "库里没有任何事件与产物，进度无法推断（不能视为「时间充裕」）。",
            "先跑 `python -m mmagent.cli intake --problem <题面>` 或 `cli solve`，"
            "让 artifacts 表出现至少 1 行后再看进度。"))
    if idle_hours is not None and idle_hours >= IDLE_THRESHOLD_HOURS:
        out.append(_warning(
            "IDLE_NO_OUTPUT",
            f"已连续 {idle_hours:.1f} h 无新产出（阈值 {IDLE_THRESHOLD_HOURS:.0f} h）。",
            "降级为最短可交版本：只保 P0 三件套（体检报告/求解结果/图），"
            "立刻跑 solve/plot 重新产出并登记 artifacts。"))
    if remaining_hours is not None and remaining_hours <= MIN_VIABLE_AT_HOURS:
        out.append(_warning(
            "MIN_VIABLE",
            f"距截止仅剩 {remaining_hours:.1f} h（阈值 {MIN_VIABLE_AT_HOURS:.0f} h），"
            "转入最短可交版本。",
            "按返回的 min_viable 清单收口：先落论文 P0 章节与 3 张必需图，"
            "其余内容一律停止投入。"))
    if remaining_hours is not None and remaining_hours <= NOT_DO_AT_HOURS:
        out.append(_warning(
            "NOT_DO_LIST",
            f"距截止仅剩 {remaining_hours:.1f} h（阈值 {NOT_DO_AT_HOURS:.0f} h），"
            "触发不做清单。",
            "把 not_do_list 各项移出本轮范围，并在交付说明里逐条写明「不做」及替代方案。"))
    if deadline_missing:
        out.append(_warning(
            "DEADLINE_MISSING",
            "调用方未提供 deadline，倒计时与「前 6 h / 前 4 h」规则均无法判定。",
            "传入 deadline（ISO8601，如 2026-10-20T22:00:00Z）后重跑 progress_status。"))
    return out


# ---------------------------------------------------------------- 主入口

def progress_status(*, db_path: str | Path = DEFAULT_DB_PATH,
                    deadline=None, now=None,
                    profile_id: str | int | None = None) -> dict:
    """FR-8 最小形态：返回倒计时、无产出预警、不做清单与最短可交版本。

    参数：
    - `db_path`：只读打开的 SQLite 库（可传相对路径）；文件不存在抛 `FileNotFoundError`。
    - `deadline`：ISO8601 字符串或 `datetime`；`None` → 时间类规则一律不触发。
    - `now`：**测试恒注入**；`None` 时才取当前 UTC 时间（本函数唯一读时钟处）。
    - `profile_id`：`str` / `int` 均可（两侧按文本比较）。给定时 `artifacts` 按列过滤、
      `events` 按 `payload.$.profile_id` 过滤；过滤后为空但全库有行 → `PROFILE_FILTER_EMPTY`。
    """
    now_dt = _parse_ts(now) if now is not None else datetime.now(timezone.utc)
    deadline_dt = _parse_ts(deadline) if deadline is not None else None
    deadline_missing = deadline_dt is None
    remaining_hours = (_hours_between(deadline_dt, now_dt)
                       if deadline_dt is not None else None)

    conn = _connect_ro(db_path)
    try:
        event_count = _count(conn, "events", profile_id)
        artifact_count = _count(conn, "artifacts", profile_id)
        last_event = _last_event(conn, profile_id)          # 仅用于 stage
        artifact_ts = _max_ts(conn, "artifacts", profile_id)
        event_ts = _max_ts(conn, "events", profile_id)      # 最后活动时刻（按 ts，不按 id）
        # 全库行数：用于暴露"过滤后为空、但库里其实有数据"的不对称（现网 events/artifacts
        # 写入时普遍不带 profile_id，过滤口径会把 235 条产物报成空库）。
        if profile_id is not None and str(profile_id) != "":
            unfiltered_event_count = _count(conn, "events", None)
            unfiltered_artifact_count = _count(conn, "artifacts", None)
        else:
            unfiltered_event_count, unfiltered_artifact_count = event_count, artifact_count
    finally:
        conn.close()

    if artifact_ts is not None:
        last_output_raw, last_output_source = artifact_ts, "artifact"
    elif event_ts is not None:
        last_output_raw, last_output_source = event_ts, "event"
    else:
        last_output_raw, last_output_source = None, None

    last_output_at = _parse_ts(last_output_raw) if last_output_raw else None
    # 未来时间戳（时钟偏移）按 0 计，不产生负空闲
    idle_hours = (max(0.0, _hours_between(now_dt, last_output_at))
                  if last_output_at is not None else None)

    warnings = _build_warnings(
        event_count=event_count, artifact_count=artifact_count,
        idle_hours=idle_hours, remaining_hours=remaining_hours,
        deadline_missing=deadline_missing, profile_id=profile_id,
        unfiltered_event_count=unfiltered_event_count,
        unfiltered_artifact_count=unfiltered_artifact_count)
    finalized = _finalize_warnings(warnings)

    min_viable = None
    if remaining_hours is not None and remaining_hours <= MIN_VIABLE_AT_HOURS:
        min_viable = {
            "source": MIN_VIABLE_SOURCE,
            "chapters": list(MIN_VIABLE_CHAPTERS),
            "figures": list(MIN_VIABLE_FIGURES),
        }

    not_do_list = ([dict(item) for item in NOT_DO_ITEMS]
                   if remaining_hours is not None and remaining_hours <= NOT_DO_AT_HOURS
                   else [])

    return {
        "now": _fmt_ts(now_dt),
        "deadline": _fmt_ts(deadline_dt) if deadline_dt is not None else None,
        "remaining_hours": remaining_hours,
        "deadline_missing": deadline_missing,
        "last_output_at": _fmt_ts(last_output_at) if last_output_at is not None else None,
        "last_output_source": last_output_source,
        "idle_hours": idle_hours,
        "stage": _stage_of(last_event),
        "event_count": event_count,
        "artifact_count": artifact_count,
        "unfiltered_event_count": unfiltered_event_count,
        "unfiltered_artifact_count": unfiltered_artifact_count,
        "warnings": finalized["warnings"],
        "actionable_ratio": finalized["actionable_ratio"],
        "not_do_list": not_do_list,
        "not_do_source": NOT_DO_SOURCE if not_do_list else None,
        "min_viable": min_viable,
        "invalid_warnings": finalized["invalid_warnings"],
    }
