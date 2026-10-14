"""命令行入口：python -m mmagent.cli <子命令>。

子命令：
- db init --db data\\mm.db          建库（schema.sql）
- intake --dataset <dir> --out <dir> 附件体检
- env-attest --out <path>          环境留存（python/依赖版本 + Ollama 模型列表）
- netguard-check                   静态扫描仓库 *.py 是否违规直连外网
- run start|resume|status          人机回环（LangGraph interrupt + SqliteSaver）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path

# ---------------------------------------------------------------- 常量

REPO_ROOT = Path(__file__).resolve().parent.parent
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
TRACKED_DEPS = ["fastapi", "uvicorn", "pandas", "openpyxl", "xlrd", "matplotlib",
                "python-docx", "pydantic", "PyYAML", "httpx", "Jinja2", "pytest",
                # task-002 人机回环：本地 LangGraph 版本须可追溯（零外网）
                "langgraph", "langgraph-checkpoint-sqlite"]
BANNED_NET_IMPORTS = ("requests", "socket", "urllib", "ftplib")


# ---------------------------------------------------------------- 工具函数

def _dep_versions() -> dict:
    """读取关键依赖的已装版本（未装记为 None）。"""
    out = {}
    for name in TRACKED_DEPS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def _ollama_models() -> dict:
    """直连本地 Ollama 拉模型列表；连不上返回 available=False（不抛异常）。"""
    try:
        import httpx

        resp = httpx.get(OLLAMA_TAGS_URL, timeout=3.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return {"available": True, "base_url": OLLAMA_TAGS_URL, "models": models}
    except Exception as exc:
        return {"available": False, "base_url": OLLAMA_TAGS_URL, "error": str(exc)}


def _iter_repo_py(scan_root: Path, exclude: set[Path]) -> list[Path]:
    """遍历仓库内 *.py，排除给定文件（扫描器自身）、.venv 与 .agent-runs。

    `.agent-runs/` 已被 git 跟踪（调查/测试/验证的探针脚本），其中合法地会出现
    `import socket` / `requests` 等字样，不应被当成产品代码误报（task-002 §3.9）。
    """
    files = []
    for p in scan_root.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", ".git", ".agent-runs"} for part in p.parts):
            continue
        if p.resolve() in exclude:
            continue
        files.append(p)
    return sorted(files)


def _net_violations(path: Path) -> list[str]:
    """AST 检测文件是否**真实** import 了被禁的联网库。

    用 AST 而非文本正则：测试数据/文档字符串里出现 `import requests` 这类字面量
    不算违规，只有真实的 import 语句才拦截（避免误报 tests/ 下的守卫用例）。
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return [f"{path}: 解析失败（{exc.msg}），未做联网检查"]
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_NET_IMPORTS:
                    hits.append(f"{path}:{node.lineno}: 违规 import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_NET_IMPORTS:
                hits.append(f"{path}:{node.lineno}: 违规 from {node.module} import ...")
    return hits


# ---------------------------------------------------------------- 子命令

def cmd_db_init(args) -> int:
    """建库：执行 store/schema.sql。"""
    from .store.db import init_db

    path = init_db(args.db)
    print(f"[db init] 已初始化：{path}")
    return 0


def cmd_intake(args) -> int:
    """附件体检。"""
    from .pipeline.intake import run_intake_audit

    summary = run_intake_audit(args.dataset, args.out)
    print(f"[intake] 表数={len(summary['tables'])} 字段数={summary['field_count']} "
          f"冲突数={summary['conflict_count']} → {summary['out_dir']}")
    return 0


def cmd_env_attest(args) -> int:
    """环境留存：python、依赖、Ollama 模型列表。"""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    deps = _dep_versions()
    ollama = _ollama_models()
    req_path = REPO_ROOT / "requirements.txt"
    req_hash = (hashlib.sha256(req_path.read_bytes()).hexdigest()[:16]
                if req_path.exists() else None)
    attest = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "dependencies": deps,
        "requirements_sha256_16": req_hash,
        "ollama": ollama,
    }
    out.write_text(json.dumps(attest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[env-attest] 已写入：{out}（ollama available={ollama['available']}）")
    return 0


def cmd_netguard_check(args) -> int:
    """静态扫描仓库 *.py，检出违规直连外网的 import。"""
    exclude = {Path(__file__).resolve()}
    violations = []
    for path in _iter_repo_py(REPO_ROOT, exclude):
        violations.extend(_net_violations(path))
    if violations:
        print("[netguard-check] 发现违规：")
        for v in violations:
            print("  " + v)
        return 1
    print("[netguard-check] 通过：未发现 requests/socket/urllib/ftplib 直连")
    return 0


def cmd_desensitize_check(args) -> int:
    """NFR-7 脱敏检查：逐行扫描输入文件，命中即报告并把记录写入 desensitize_hit。"""
    from .security.desensitize import record_hits, scan_and_mask

    src = Path(args.input)
    if not src.exists():
        print(f"[desensitize-check] 输入文件不存在：{src}")
        return 2
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    _, hits = scan_and_mask(lines, field_raw=args.field or "", table_id=args.table or "")

    masked_lines, _ = scan_and_mask(lines, field_raw=args.field or "",
                                    table_id=args.table or "")
    if args.mask_out:
        out = Path(args.mask_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(masked_lines) + "\n", encoding="utf-8")
        print(f"[desensitize-check] 脱敏副本已写入：{out}")

    if hits:
        record_hits(hits, db_path=args.db)
        print(f"[desensitize-check] 命中 {len(hits)} 处（已记入 desensitize_hit），明细：")
        for h in hits:
            print(f"  - rule={h['rule']} field={h['field_raw'] or '<无列名>'} "
                  f"action={h['action']} sha={h['raw_sha256']}")
        return 1
    print(f"[desensitize-check] 通过：{len(lines)} 行未命中敏感模式")
    return 0


# ---------------------------------------------------------------- 人机回环（run）

def _events_table_present(db_path: str) -> bool:
    """检测台账库是否已初始化（存在 events 表）。

    不自动建库——避免静默产生新文件（那正是本项目要消灭的"安静地丢数据"）。
    """
    import sqlite3

    path = Path(db_path)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _require_ledger(db_path: str) -> int | None:
    """台账库未初始化时打印明确错误并返回 exit code 3；就绪返回 None。"""
    if _events_table_present(db_path):
        return None
    print(
        f"[run] 台账库 {db_path} 缺少 events 表（尚未初始化），事件留痕将无法落库。\n"
        f"      请先执行：python -m mmagent.cli db init --db {db_path}",
        file=sys.stderr)
    return 3


# `--conflicts` / `--sanity-blocking` 是**模拟开关**（真实待审材料应由上层注入）；
# 未显式提供明细时，用与项目已知数据问题（README §9）一致的示例材料，便于人工看到"该审什么"。
_DEFAULT_CONFLICTS_REVIEW = [
    {"field": "评级",
     "reason": "同构外推列口径冲突（示例，见 README §9 已知数据问题 #3）",
     "candidates": ["qual_1_2_7_8_10", "def_7_8"]},
]
_DEFAULT_SANITY_REVIEW = [
    {"check": "unit_mismatch",
     "detail": "示例：单位/量纲不一致，需人工裁决后继续"},
]


def _parse_review_json(text: str, label: str):
    """解析可选的待审材料 JSON；非法时打印错误并返回 (None, 2)，合法返回 (值, None)。"""
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        print(f"[run] --{label} 不是合法 JSON：{exc}", file=sys.stderr)
        return None, 2


def cmd_run_start(args) -> int:
    """启动一次运行；命中人工卡点属**正常状态** → exit 0 并打印结构化 JSON。"""
    from .agents.coordinator import CheckpointUnavailable, Coordinator

    code = _require_ledger(args.db)
    if code is not None:
        return code
    context: dict = {}
    if args.conflicts:
        context["caliber_conflicts"] = True
        if args.conflict_detail:
            detail, err = _parse_review_json(args.conflict_detail, "conflict-detail")
            if err is not None:
                return err
            context["conflicts"] = detail
        else:
            context["conflicts"] = _DEFAULT_CONFLICTS_REVIEW
    if args.sanity_blocking:
        context["sanity_blocking"] = True
        if args.sanity_detail:
            detail, err = _parse_review_json(args.sanity_detail, "sanity-detail")
            if err is not None:
                return err
            context["sanity_items"] = detail
    try:
        coord = Coordinator(db_path=args.db, ckpt_path=args.ckpt)
    except CheckpointUnavailable as exc:
        print(f"[run start] {exc}", file=sys.stderr)
        return 3
    try:
        status = coord.start(args.run_id, context)
    except CheckpointUnavailable as exc:
        print(f"[run start] {exc}", file=sys.stderr)
        return 3
    finally:
        coord.close()
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0


def cmd_run_resume(args) -> int:
    """投递人工裁定恢复；裁定非法/投错 gate → exit 2；台账库未初始化/检查点库不可用 → exit 3。"""
    from .agents.coordinator import CheckpointUnavailable, Coordinator, InvalidGateDecision

    code = _require_ledger(args.db)
    if code is not None:
        return code
    try:
        decision = json.loads(args.decision)
    except json.JSONDecodeError as exc:
        print(f"[run resume] decision 不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    try:
        coord = Coordinator(db_path=args.db, ckpt_path=args.ckpt)
    except CheckpointUnavailable as exc:
        print(f"[run resume] {exc}", file=sys.stderr)
        return 3
    try:
        status = coord.resume(args.run_id, args.gate, decision)
    except InvalidGateDecision as exc:
        print(f"[run resume] {exc}", file=sys.stderr)
        return 2
    except CheckpointUnavailable as exc:
        print(f"[run resume] {exc}", file=sys.stderr)
        return 3
    finally:
        coord.close()
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0


def cmd_run_status(args) -> int:
    """打印当前阶段与是否挂起（不推进图）；run_id 不存在 → exit 4。"""
    from .agents.coordinator import CheckpointUnavailable, Coordinator, UnknownRunError

    code = _require_ledger(args.db)
    if code is not None:
        return code
    try:
        coord = Coordinator(db_path=args.db, ckpt_path=args.ckpt)
    except CheckpointUnavailable as exc:
        print(f"[run status] {exc}", file=sys.stderr)
        return 3
    try:
        status = coord.status(args.run_id)
    except UnknownRunError as exc:
        print(f"[run status] {exc}", file=sys.stderr)
        return 4
    except CheckpointUnavailable as exc:
        print(f"[run status] {exc}", file=sys.stderr)
        return 3
    finally:
        coord.close()
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器。"""
    parser = argparse.ArgumentParser(prog="mmagent", description="数模协作 Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_db = sub.add_parser("db", help="数据库操作")
    db_sub = p_db.add_subparsers(dest="db_command", required=True)
    p_db_init = db_sub.add_parser("init", help="初始化数据库")
    p_db_init.add_argument("--db", default="data/mm.db", help="数据库路径")
    p_db_init.set_defaults(func=cmd_db_init)

    p_intake = sub.add_parser("intake", help="附件体检")
    p_intake.add_argument("--dataset", required=True, help="数据集目录")
    p_intake.add_argument("--out", required=True, help="产物输出目录")
    p_intake.set_defaults(func=cmd_intake)

    p_env = sub.add_parser("env-attest", help="环境留存")
    p_env.add_argument("--out", required=True, help="留存 JSON 路径")
    p_env.set_defaults(func=cmd_env_attest)

    p_net = sub.add_parser("netguard-check", help="静态检查外网 import")
    p_net.set_defaults(func=cmd_netguard_check)

    p_des = sub.add_parser("desensitize-check", help="NFR-7 脱敏扫描")
    p_des.add_argument("--input", required=True, help="待扫描文本文件（逐行扫描）")
    p_des.add_argument("--field", default="", help="列名（可选，用于列级规则）")
    p_des.add_argument("--table", default="", help="表 id（可选）")
    p_des.add_argument("--mask-out", default="", help="脱敏副本输出路径（可选）")
    p_des.add_argument("--db", default="data/mm.db", help="命中台账数据库路径")
    p_des.set_defaults(func=cmd_desensitize_check)

    p_run = sub.add_parser("run", help="人机回环运行（LangGraph interrupt + SqliteSaver）")
    run_sub = p_run.add_subparsers(dest="run_command", required=True)

    p_run_start = run_sub.add_parser("start", help="启动一次运行，遇卡点即挂起（exit 0）")
    p_run_start.add_argument("--run-id", required=True, help="运行 id（= langgraph thread_id）")
    p_run_start.add_argument("--conflicts", action="store_true", help="注入口径冲突（触发卡点1）")
    p_run_start.add_argument("--conflict-detail", default="",
                             help="口径冲突待审材料（JSON，可选；缺省用内置示例）")
    p_run_start.add_argument("--sanity-blocking", action="store_true",
                             help="注入 sanity 阻断（触发卡点2）")
    p_run_start.add_argument("--sanity-detail", default="",
                             help="sanity 阻断待审材料（JSON，可选）")
    p_run_start.add_argument("--ckpt", default="data/checkpoints.db", help="检查点库路径")
    p_run_start.add_argument("--db", default="data/mm.db", help="事件台账库路径")
    p_run_start.set_defaults(func=cmd_run_start)

    p_run_resume = run_sub.add_parser("resume", help="投递人工裁定，从挂起点恢复")
    p_run_resume.add_argument("--run-id", required=True, help="运行 id（= langgraph thread_id）")
    p_run_resume.add_argument("--gate", required=True, help="卡点名（须与当前挂起一致）")
    p_run_resume.add_argument("--decision", required=True, help="JSON 字符串裁定")
    p_run_resume.add_argument("--ckpt", default="data/checkpoints.db", help="检查点库路径")
    p_run_resume.add_argument("--db", default="data/mm.db", help="事件台账库路径")
    p_run_resume.set_defaults(func=cmd_run_resume)

    p_run_status = run_sub.add_parser("status", help="查看当前阶段与是否挂起")
    p_run_status.add_argument("--run-id", required=True, help="运行 id（= langgraph thread_id）")
    p_run_status.add_argument("--ckpt", default="data/checkpoints.db", help="检查点库路径")
    p_run_status.add_argument("--db", default="data/mm.db", help="事件台账库路径")
    p_run_status.set_defaults(func=cmd_run_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口，返回退出码。"""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
