"""FR-13 合规声明：生成 ai_disclosure.md 与 ai_disclosure.docx。

四字段（竞赛组委会要求）：
1. Agent 参与范围
2. 人改比例（由 Python 计算：人工事件数 / 总事件数；无数据时用传入值或默认 0.3）
3. 模型与提示词版本（从 artifacts/metric_obs 查询；查不到写占位）
4. 数据与素材来源声明

**运行方式陈述由数据渲染，不得再写成常量**（task-005 / D-18）：
`_runtime_backend_facts()` 读 `config/models.yaml` 的 `provider` / `net_allowlist` + `net_audit` 台账，
按 §3.2 的分支渲染同一份 `statement`，md 模板与 docx 段落**共用该变量**。
读不到配置或台账时降级为 `unknown` / 0，并把原因留在 `degraded`，**绝不伪造成“无云端调用”**。

**「未读」必须与「读且为 0」分开**（task-006 / D-20）：
`connect()` 会自动建库，故台账可用性必须在 `connect()` **之前**用 `Path(db_path).exists()` 判定；
台账不存在/不可读时 `ledger_available=False`，只能报「未能读取台账，无法确认」，
**禁止**输出 `0 次` 或「未使用云端 API 调用」。同时不再为读台账而凭空建库。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from ..store.db import DEFAULT_DB_PATH, connect, utcnow

DEFAULT_HUMAN_EDIT_RATIO = 0.3  # 无事件数据时的保守默认值（人工复核兜底）
#: 运行时后端配置（与 `cli` / `agents` 读同一份文件）
DEFAULT_MODELS_CONFIG = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _render_hosts(hosts: list[str]) -> str:
    """把 net_allowlist 渲染成顿号连接；空名单显式写「名单为空」。"""
    return "、".join(hosts) if hosts else "（名单为空）"


def _hosts_have_loopback(hosts: list[str]) -> bool:
    return any(h.strip().lower() in _LOOPBACK_HOSTS for h in hosts)


def _render_counts(net_calls: int, net_ok: int, ledger_available: bool) -> str:
    """`{counts}` 的**唯一来源**：已读报文数 / 未读报「无法确认」。

    未读时不得出现 `0 次`——那会把「没测到」写成「测到了 0」（task-006 / D-20）。
    """
    if ledger_available:
        return f"net_audit 台账已记录调用 {net_calls} 次（成功 {net_ok} 次）"
    return "**未能读取 net_audit 台账，无法确认云端调用次数**"


def _render_runtime_statement(provider: str, hosts: list[str],
                              net_calls: int, net_ok: int,
                              ledger_available: bool = True) -> str:
    """按 task-006 §3.2 的六条分支渲染「运行方式」陈述。

    三态永不混淆：已读且为 0 / 已读且不为 0 / **未读**。
    `ollama` 的「未使用云端 API 调用」只有在 `ledger_available and net_calls == 0` 时才允许；
    已知存在云端调用时必须说「与配置不一致」，不得说「无法确认」（那是把已知说成未知）。
    """
    counts = _render_counts(net_calls, net_ok, ledger_available)
    if provider == "unknown":
        return (f"运行方式：后端未知（config/models.yaml 未能读出 provider）；"
                f"{counts}。**该状态需人工核实后修订本声明。**")
    if provider == "ollama" and ledger_available and net_calls == 0:
        return "运行方式：本地推理（Ollama，localhost:11434），未使用云端 API 调用。"
    if provider == "ollama" and not ledger_available:
        # 未读：只能说「无法确认」，且不重复一次「未能读取」（{counts} 已含同义句）。
        return ("运行方式：本地推理（Ollama，localhost:11434）；"
                "**未能读取 net_audit 台账，无法确认是否存在云端调用，"
                "需人工核实后修订本声明。**")
    if provider == "ollama":
        # 已知存在云端调用：说「已知」且指出与配置矛盾，不得再说「无法确认」。
        return (f"运行方式：本地推理（Ollama，localhost:11434）；{counts}"
                f"；**但台账中存在云端调用记录，与 provider=ollama 配置不一致，"
                f"需人工核实后修订本声明。**")
    if provider == "dashscope":
        # 名单原样列出（不删任何项，尤其是回环地址——删掉就是掩盖配置），只补一句限定。
        loopback_note = ("（含本机回环地址；出网白名单以 config/models.yaml 为准）"
                         if _hosts_have_loopback(hosts) else "")
        return (f"运行方式：调用云端模型服务（provider=dashscope，host 位于名单 "
                f"{_render_hosts(hosts)}{loopback_note}）；{counts}。")
    # 已读出 provider 但不匹配上面几条已知分支（如 openai）：
    # 如实点名后端，不写「未知」——否则又是一句假话。
    return (f"运行方式：后端为 {provider}（config/models.yaml 已读出，但未匹配已知分支）；"
            f"{counts}。**该状态需人工核实后修订本声明。**")


def _net_audit_table_present(db_path: str | Path) -> tuple[bool, str | None]:
    """只读探测 `net_audit` 表是否存在，返回 `(present, error)`。

    **不得用 `connect()` 探测**：它会 `ensure_schema()` 自动建表，把「表不存在」洗成「0 行」。
    只读 URI（`mode=ro`）不会创建文件或写入任何内容。
    """
    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='net_audit'"
            ).fetchone()
        finally:
            conn.close()
        return row is not None, None
    except Exception as exc:
        return False, str(exc)


def _runtime_backend_facts(config_path: str | Path | None = None,
                           db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    """采集运行时后端事实，返回 `provider` / `hosts` / `net_calls` / `net_ok` /
    `statement` / `degraded`。

    - `provider`：`config/models.yaml` 顶层 `provider`；读不到 → `"unknown"`
    - `hosts`：`net_allowlist`；缺失 → `[]`
    - `net_calls` / `net_ok`：`SELECT COUNT(*), SUM(ok) FROM net_audit`（**全局，无 profile 维度**：
      `net_audit` 表不含 `profile_id`，本轮不加列）
    - `ledger_available` / `ledger_path`：台账是否**真的读过**及路径。`connect()` 会自动建库，
      故必须先用 `Path(db_path).exists()` 判定，否则「刚建的空库」会被误读成「读且为 0」
    - `degraded`：任何读取失败的原因；**降级绝不伪造成“无云端调用”**
    """
    degraded: list[str] = []
    provider = "unknown"
    hosts: list[str] = []

    path = Path(config_path) if config_path is not None else DEFAULT_MODELS_CONFIG
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"顶层不是映射：{type(data).__name__}")
        raw_provider = data.get("provider")
        if isinstance(raw_provider, str) and raw_provider.strip():
            provider = raw_provider.strip()
        else:
            degraded.append(f"models.yaml 未声明 provider（{path}）")
        allow = data.get("net_allowlist")
        if isinstance(allow, list):
            hosts = [str(h) for h in allow]
        else:
            degraded.append(f"models.yaml 未声明 net_allowlist（{path}）")
    except Exception as exc:  # 配置缺失/非法都不许中断声明生成
        degraded.append(f"models.yaml 读取失败: {exc}")

    net_calls = net_ok = 0
    ledger_available = False
    # 必须在 connect() 之前判：connect() 会 ensure_schema() 自动建库，事后无法区分
    # 「刚建的空库」与「本就有数据的库」（task-006 / D-20）。
    if not Path(db_path).exists():
        degraded.append(f"net_audit 台账不存在，未读取（{db_path}）；不视为「零调用」")
    else:
        present, probe_error = _net_audit_table_present(db_path)
        if probe_error is not None:
            degraded.append(f"net_audit 台账无法读取: {probe_error}；不视为「零调用」")
        elif not present:
            degraded.append(
                f"net_audit 台账缺 net_audit 表（{db_path}），未读取；不视为「零调用」")
        else:
            try:
                conn = connect(db_path)
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) AS c, COALESCE(SUM(ok), 0) AS o FROM net_audit"
                    ).fetchone()
                    net_calls = int(row["c"] or 0)
                    net_ok = int(row["o"] or 0)
                    ledger_available = True
                finally:
                    conn.close()
            except Exception as exc:
                degraded.append(f"net_audit 读取失败: {exc}；不视为「零调用」")

    return {
        "provider": provider,
        "hosts": hosts,
        "net_calls": net_calls,
        "net_ok": net_ok,
        "ledger_available": ledger_available,
        "ledger_path": str(db_path),
        "statement": _render_runtime_statement(
            provider, hosts, net_calls, net_ok, ledger_available),
        "degraded": degraded,
    }


def _collect_llm_provenance(profile_id: str, version_id: int | None,
                            db_path: str | Path) -> dict:
    """从 artifacts / metric_obs 查模型与提示词版本；查不到返回占位。

    库文件不存在时**不 connect**：`connect()` 会自动建库，只为读溯源而凭空造出一个空库
    是副作用（task-006 / D-20）。此处占位文案已含「占位」字样，语义正确。
    """
    provenance = {"model_tags": [], "prompt_versions": []}
    if not Path(db_path).exists() or not _net_audit_table_present(db_path)[0]:
        return provenance
    try:
        conn = connect(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT model_tag FROM artifacts WHERE profile_id=? "
                "AND model_tag IS NOT NULL", (profile_id,)).fetchall()
            provenance["model_tags"] = sorted(r["model_tag"] for r in rows)
            rows = conn.execute(
                "SELECT DISTINCT prompt_version FROM metric_obs "
                "WHERE prompt_version IS NOT NULL").fetchall()
            provenance["prompt_versions"] = sorted(r["prompt_version"] for r in rows)
        finally:
            conn.close()
    except Exception:
        pass  # 库不存在或表缺失时用占位，不阻断声明生成
    return provenance


def _calc_human_edit_ratio(profile_id: str, db_path: str | Path) -> tuple[float, str]:
    """人改比例 = 人工 actor 事件数 / 总事件数；无事件时返回默认值并注明。

    库文件不存在时**不 connect**（同上，避免为生成声明而建库）。
    """
    if not Path(db_path).exists() or not _net_audit_table_present(db_path)[0]:
        return DEFAULT_HUMAN_EDIT_RATIO, "无事件数据，采用保守默认值（待人工复核修订）"
    try:
        conn = connect(db_path)
        try:
            total = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
            human = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE actor LIKE 'human%'").fetchone()["c"]
        finally:
            conn.close()
        if total > 0:
            return round(human / total, 4), "按事件流水统计（人工事件/总事件）"
    except Exception:
        pass
    return DEFAULT_HUMAN_EDIT_RATIO, "无事件数据，采用保守默认值（待人工复核修订）"


def make_ai_disclosure(profile_id: str, version_id: int | None,
                       output_dir: str | Path,
                       human_edit_ratio: float | None = None,
                       db_path: str | Path = DEFAULT_DB_PATH,
                       data_sources: list[str] | None = None,
                       config_path: str | Path | None = None) -> Path:
    """生成 ai_disclosure.md / .docx，返回 md 路径。

    `config_path` 可选（缺省读仓库 `config/models.yaml`）；仅供降级路径验证与多环境复用。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if human_edit_ratio is None:
        ratio, ratio_note = _calc_human_edit_ratio(profile_id, db_path)
    else:
        ratio, ratio_note = human_edit_ratio, "由调用方人工核定"
    prov = _collect_llm_provenance(profile_id, version_id, db_path)
    model_tags = "、".join(prov["model_tags"]) or "（占位：尚无 LLM 调用记录）"
    prompt_versions = "、".join(prov["prompt_versions"]) or "（占位：尚无提示词版本记录）"
    sources = data_sources or ["竞赛官方附件（赛题发布数据集）"]
    ts = utcnow()

    # 运行方式：**由配置 + 台账渲染**，md 与 docx 共用同一个 statement（D-18）
    runtime = _runtime_backend_facts(config_path, db_path)
    statement = runtime["statement"]
    degraded = runtime["degraded"]
    degraded_line = (f"\n- 事实采集降级：{'; '.join(degraded)}"
                     f"（本行由系统自动追加，说明上述运行方式可能不完整）") if degraded else ""
    degraded_docx = (f" 事实采集降级：{'; '.join(degraded)}"
                     f"（本行由系统自动追加，说明上述运行方式可能不完整）") if degraded else ""

    md = f"""# AI 使用披露声明

- 档案：{profile_id}（version_id={version_id if version_id is not None else 'last'}）
- 生成时间（UTC）：{ts}

## 1. Agent 参与范围

本作品在准备过程中使用了本地部署的 AI 协作 Agent（mmagent）辅助完成以下环节：
附件数据体检、字段口径冲突提示、求解脚本起草、图表渲染与论文初稿组装。
所有 AI 产出均经队员人工审阅、修改与确认，最终内容由队员全权负责。

## 2. 人改比例

- 人改比例：{ratio:.1%}
- 口径说明：{ratio_note}。

## 3. 模型与提示词版本

- 模型标签：{model_tags}
- 提示词版本：{prompt_versions}
- {statement}{degraded_line}

## 4. 数据与素材来源声明

{chr(10).join(f'- {s}' for s in sources)}

全部数据与素材来源合法合规，未使用任何未经授权的外部数据。
"""
    md_path = output_dir / "ai_disclosure.md"
    md_path.write_text(md, encoding="utf-8")

    # docx 版（与 md 同内容，供随论文提交）
    from docx import Document

    doc = Document()
    doc.add_heading("AI 使用披露声明", level=1)
    doc.add_paragraph(f"档案：{profile_id}（version_id="
                      f"{version_id if version_id is not None else 'last'}）")
    doc.add_paragraph(f"生成时间（UTC）：{ts}")
    for title, body in [
        ("1. Agent 参与范围",
         "使用本地 AI 协作 Agent（mmagent）辅助：附件数据体检、字段口径冲突提示、"
         "求解脚本起草、图表渲染与论文初稿组装。所有 AI 产出均经队员人工审阅、"
         "修改与确认，最终内容由队员全权负责。"),
        ("2. 人改比例", f"人改比例：{ratio:.1%}。口径说明：{ratio_note}。"),
        ("3. 模型与提示词版本",
         f"模型标签：{model_tags}；提示词版本：{prompt_versions}；"
         f"{statement}{degraded_docx}"),
        ("4. 数据与素材来源声明",
         "；".join(sources) + "。全部数据与素材来源合法合规。"),
    ]:
        doc.add_heading(title, level=2)
        doc.add_paragraph(body)
    doc.save(str(output_dir / "ai_disclosure.docx"))

    return md_path
