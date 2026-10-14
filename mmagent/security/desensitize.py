"""NFR-7 脱敏：姓名/学号/手机号/身份证/导师信息 5 类模式逐单元格扫描。

设计要点：
- 规则来自 `config/desensitize.yaml`（禁止在代码中硬编码正则）；
- 命中即把该单元格**整体**替换为 mask 文本（默认 `[已脱敏]`）；
- hit 记录只保留规则名与原文哈希，**不落原文**（合规要求）；
- `assert_clean(text)` 供入库前钩子调用，命中即抛 DesensitizeBlocked。
"""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

import yaml

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "desensitize.yaml"
DEFAULT_MASK = "[已脱敏]"


class DesensitizeBlocked(Exception):
    """检测到未脱敏的敏感内容，阻断入库（error_code 固定为 DESENSITIZE_BLOCKED）。"""

    error_code = "DESENSITIZE_BLOCKED"

    def __init__(self, hits: list[dict], where: str = ""):
        self.hits = hits
        self.where = where
        rules = sorted({h.get("rule", "?") for h in hits})
        super().__init__(
            f"入库被阻断：{where or '待入库内容'} 命中未脱敏敏感信息（规则：{'/'.join(rules)}）")


def load_rules(path: str | Path = CONFIG_PATH) -> list[dict]:
    """加载并编译脱敏规则（幂等，每次调用重新读文件便于配置热改）。"""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    compiled = []
    for rule in raw.get("rules", []):
        item = dict(rule)
        if item.get("pattern"):
            item["_re"] = re.compile(item["pattern"])
        if item.get("header_pattern"):
            item["_header_re"] = re.compile(item["header_pattern"])
        if item.get("cell_pattern"):
            item["_cell_re"] = re.compile(item["cell_pattern"])
        item.setdefault("mask", DEFAULT_MASK)
        compiled.append(item)
    return compiled


def _hash(text: str) -> str:
    """敏感原文只留 sha256 前 16 位，避免原文入库。"""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def _hit(rule: dict, table_id: str, field_raw: str, original: str,
         action: str) -> dict:
    return {
        "rule": rule["name"],
        "rule_label": rule.get("label", rule["name"]),
        "table_id": table_id,
        "field_raw": field_raw,
        "masked_value": rule.get("mask", DEFAULT_MASK),
        "raw_sha256": _hash(original),   # 仅哈希，不落原文
        "action": action,
    }


def scan_and_mask(cells: list[str], field_raw: str = "",
                  table_id: str = "") -> tuple[list[str], list[dict]]:
    """逐单元格扫描并按需脱敏。

    返回 (masked_cells, hits)。行为：
    - `target: cell` 规则：单元格匹配 pattern（若配置了 contexts 还需含任一关键词）
      则**整格**替换为 mask；
    - `target: column` 规则：`field_raw`（或首元素）命中列头 pattern 时整列脱敏（列头保留），
      否则对每格跑 cell_pattern；
    - 同一单元格只记首个命中规则，避免重复计数。
    """
    rules = load_rules()
    masked = [("" if c is None else str(c)) for c in cells]
    hits: list[dict] = []
    masked_index: set[int] = set()

    for rule in rules:
        if rule.get("target") == "column":
            header = field_raw or (masked[0] if masked else "")
            if rule.get("_header_re") and rule["_header_re"].match(header or ""):
                # 整列脱敏：保留列头，其余非空单元格替换
                start = 0 if field_raw else 1
                for i in range(start, len(masked)):
                    if masked[i] == "":
                        continue
                    hits.append(_hit(rule, table_id, header, masked[i], "mask_column"))
                    masked[i] = rule.get("mask", DEFAULT_MASK)
                    masked_index.add(i)
                continue
            if rule.get("_cell_re"):
                for i, cell in enumerate(masked):
                    if i in masked_index or cell == "":
                        continue
                    if rule["_cell_re"].search(cell):
                        hits.append(_hit(rule, table_id, field_raw, cell, "replaced"))
                        masked[i] = rule.get("mask", DEFAULT_MASK)
                        masked_index.add(i)
            continue

        # target == cell
        for i, cell in enumerate(masked):
            if i in masked_index or cell == "":
                continue
            if not rule["_re"].search(cell):
                continue
            contexts = rule.get("contexts") or []
            if contexts and not any(k.lower() in cell.lower() for k in contexts):
                continue
            hits.append(_hit(rule, table_id, field_raw, cell, "replaced"))
            masked[i] = rule.get("mask", DEFAULT_MASK)
            masked_index.add(i)

    return masked, hits


def scan_column(field_raw: str, cells: list[str], table_id: str = "") -> tuple[list[str], list[dict]]:
    """按「列头 + 列值」扫描（列头别名，语义等价于 scan_and_mask(cells, field_raw, table_id)）。"""
    return scan_and_mask(cells, field_raw=field_raw, table_id=table_id)


def assert_clean(text: str, db_path: str | Path = DEFAULT_DB_PATH,
                 where: str = "") -> None:
    """入库前钩子：文本命中任一脱敏规则即抛 DesensitizeBlocked（并尽力落库命中记录）。"""
    _, hits = scan_and_mask([text])
    if hits:
        record_hits(hits, db_path=db_path)
        raise DesensitizeBlocked(hits, where=where)


def record_hits(hits: list[dict], db_path: str | Path = DEFAULT_DB_PATH,
                trace_id: str = "") -> int:
    """把命中记录写入 desensitize_hit 表（尽力而为，失败不抛）。"""
    if not hits:
        return 0
    trace_id = trace_id or uuid.uuid4().hex
    try:
        conn = connect(db_path)
        try:
            for h in hits:
                conn.execute(
                    "INSERT INTO desensitize_hit (ts, rule, table_id, field_raw,"
                    " masked_value, action, trace_id, raw_sha256) VALUES (?,?,?,?,?,?,?,?)",
                    (utcnow(), h["rule"], h.get("table_id", ""),
                     h.get("field_raw", ""), h.get("masked_value", DEFAULT_MASK),
                     h.get("action", "replaced"), trace_id, h.get("raw_sha256", "")))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return 0
    return len(hits)
